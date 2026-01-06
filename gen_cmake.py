import json
import os
import glob
import sys
import argparse
import re

class CMakeGenerator:
    def __init__(self, root_dir):
        self.root_dir = os.path.abspath(root_dir)
        self.third_party_dir = os.path.join(self.root_dir, "3rdparty")
        self.processed_projects = {}

    def get_relative_path(self, target_path, base_path):
        return os.path.relpath(target_path, base_path).replace("\\", "/")

    def expand_env_vars(self, path):
        # Support ${VAR} and %VAR%
        path = re.sub(r'\$\{([A-Za-z0-9_]+)\}', lambda m: os.environ.get(m.group(1), ""), path)
        path = re.sub(r'%([A-Za-z0-9_]+)%', lambda m: os.environ.get(m.group(1), ""), path)
        return path

    def collect_source_files(self, project_dir, source_dirs):
        sources = []
        extensions = ['*.cpp', '*.c', '*.cc', '*.h', '*.hpp', '*.hpp']
        for sdir in source_dirs:
            abs_sdir = os.path.join(project_dir, sdir)
            if not os.path.exists(abs_sdir):
                print(f"Warning: Source directory {abs_sdir} does not exist.")
                continue
            for ext in extensions:
                pattern = os.path.join(abs_sdir, '**', ext)
                files = glob.glob(pattern, recursive=True)
                sources.extend([self.get_relative_path(f, project_dir) for f in files])
        return sorted(list(set(sources)))

    def check_import_method(self, tp_path):
        # 0. Project JSON: Look for Project.json (HIGHEST PRIORITY)
        # If a Project.json exists, we want to control the build using our tool, 
        # regardless of whether other build files (generated or not) exist.
        if os.path.exists(os.path.join(tp_path, "Project.json")):
            return "PROJECT", tp_path

        # 1. Module Mode: Look for Find<Name>.cmake
        tp_name = os.path.basename(tp_path)
        if os.path.exists(os.path.join(tp_path, f"Find{tp_name}.cmake")):
             return "MODULE", tp_path
        
        # 2. Config Mode: Look for *Config.cmake recursively
        # We limit depth to avoid excessive scanning, e.g. 3 levels
        # Common paths: ., lib/cmake/<name>, share/cmake/<name>, cmake
        for root, dirs, files in os.walk(tp_path):
            # Check files
            for f in files:
                if f.lower().endswith("config.cmake") or f.lower().endswith("-config.cmake"):
                    return "CONFIG", root
            
            # optimization: don't go too deep or into build dirs
            depth = root[len(tp_path):].count(os.sep)
            if depth > 3:
                del dirs[:] 
        
        # 3. CMake Source: Look for CMakeLists.txt
        if os.path.exists(os.path.join(tp_path, "CMakeLists.txt")):
            return "SOURCE", tp_path
        
        # 3. CMake Source: Look for CMakeLists.txt
        if os.path.exists(os.path.join(tp_path, "CMakeLists.txt")):
            return "SOURCE", tp_path
            
        return "UNKNOWN", None

    def resolve_dependency(self, dep_path, project_dir):
        # Resolve path: Check absolute, then relative to project, then relative to 3rdparty
        resolved_path = dep_path
        if not os.path.isabs(resolved_path):
            # Try relative to project
            p = os.path.join(project_dir, dep_path)
            if os.path.exists(p):
                resolved_path = os.path.abspath(p)
            else:
                # Try relative to 3rdparty
                p = os.path.join(self.third_party_dir, dep_path)
                if os.path.exists(p):
                    resolved_path = os.path.abspath(p)
                else:
                    return None, None, None

        if not os.path.isdir(resolved_path):
             return None, None, None

        method, location = self.check_import_method(resolved_path)
        return resolved_path, method, location

    def process_project(self, project_dir, is_root=False):
        project_dir = os.path.abspath(project_dir)
        project_json_path = os.path.join(project_dir, "Project.json")
        
        if not os.path.exists(project_json_path):
            print(f"Error: Project.json not found in {project_dir}")
            sys.exit(1)

        if project_json_path in self.processed_projects:
            return self.processed_projects[project_json_path]

        with open(project_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        name = data["name"]
        version = data.get("version", "1.0.0")
        
        exec_config = data.get("executable", {"compile": False})
        lib_config = data.get("library", {"compile": False})
        
        should_compile_exec = exec_config.get("compile", False)
        should_compile_lib = lib_config.get("compile", False)

        primary_target = None
        if should_compile_lib:
            primary_target = f"{name}Lib"
        elif should_compile_exec:
            primary_target = name
        
        self.processed_projects[project_json_path] = primary_target

        # Combine internal and third-party dependencies
        raw_deps = data.get("dependencies", [])
        
        valid_deps_targets = []
        dep_cmake_cmds = []

        for dep in raw_deps:
            # Expand env vars
            dep = self.expand_env_vars(dep)
            
            abs_dep_path, method, location = self.resolve_dependency(dep, project_dir)
            
            if not abs_dep_path:
                 print(f"Error: Dependency '{dep}' not found.")
                 sys.exit(1)
            
            dep_name = os.path.basename(abs_dep_path)
            escaped_loc = location.replace('\\', '/') if location else ""
            escaped_dep_path = abs_dep_path.replace('\\', '/')

            if method == "MODULE":
                # Find<Name>.cmake found
                dep_cmake_cmds.append(f"list(APPEND CMAKE_MODULE_PATH \"{escaped_dep_path}\")")
                dep_cmake_cmds.append(f"find_package({dep_name} REQUIRED)")
                valid_deps_targets.append(dep_name)
                
            elif method == "CONFIG":
                # Config file found
                dep_cmake_cmds.append(f"find_package({dep_name} REQUIRED PATHS \"{escaped_loc}\")")
                valid_deps_targets.append(dep_name)
                
            elif method == "SOURCE":
                # CMakeLists.txt found
                dep_cmake_cmds.append(f"if(NOT TARGET {dep_name})")
                dep_cmake_cmds.append(f"    add_subdirectory(\"{escaped_dep_path}\" \"${{CMAKE_BINARY_DIR}}/deps/{dep_name}\")")
                dep_cmake_cmds.append(f"endif()")
                valid_deps_targets.append(dep_name)
                
            elif method == "PROJECT":
                # Project.json found. Recursively generate it!
                dep_target = self.process_project(abs_dep_path, is_root=False)
                # Now add it
                dep_cmake_cmds.append(f"if(NOT TARGET {dep_target})")
                dep_cmake_cmds.append(f"    add_subdirectory(\"{escaped_dep_path}\" \"${{CMAKE_BINARY_DIR}}/deps/{dep_name}\")")
                dep_cmake_cmds.append(f"endif()")
                if dep_target:
                    valid_deps_targets.append(dep_target)
            else:
                print(f"Error: Could not determine how to import dependency '{dep_name}' at {abs_dep_path}")
                sys.exit(1)

        sources = self.collect_source_files(project_dir, data.get("source_dirs", []))
        
        cmake_content = [
            f"cmake_minimum_required(VERSION 3.10)",
            f"project({name} VERSION {version})",
            "",
            f"add_definitions(-DRootPath=\"{project_dir.replace('\\', '/')}\")",
            ""
        ]

        # Use C++17 by default for consistency
        cmake_content.append("set(CMAKE_CXX_STANDARD 17)")
        cmake_content.append("set(CMAKE_CXX_STANDARD_REQUIRED ON)")
        cmake_content.append("")

        # Handle explicit install prefix
        if should_compile_lib and lib_config.get("install_dir"):
             install_dir = lib_config.get("install_dir")
             abs_install_dir = os.path.abspath(os.path.join(project_dir, install_dir))
             cmake_content.append(f"set(CMAKE_INSTALL_PREFIX \"{abs_install_dir.replace('\\', '/')}\" CACHE PATH \"Install prefix\" FORCE)")
             cmake_content.append("")

        # Add Import/Dependency Commands
        # For internal deps, if this is a standalone build (is_root or just ensuring self-contained),
        # we should add_subdirectory them to ensure targets exist.
        # However, typically in a solution build, the root adds them. 
        # But per requirements: "All involved internal libraries must be included".
        # To be safe, we can use if(NOT TARGET) check or similar, OR just rely on the fact that
        # if we are the root, we add them. 
        
        # Actually, standard CMake practice: check if target exists, if not add_subdirectory.
        # This allows both standalone and solution builds.
        
 

        if dep_cmake_cmds:
            cmake_content.extend(dep_cmake_cmds)
            cmake_content.append("")
        cmake_content.append("")

        raw_include_dirs = data.get("include_dirs", [])
        include_dirs = []
        for idir in raw_include_dirs:
            # Expand env vars first
            idir = self.expand_env_vars(idir)
            # Resolve to absolute path immediately
            abs_idir = os.path.abspath(os.path.join(project_dir, idir)).replace('\\', '/')
            include_dirs.append(abs_idir)

        # Combine linker dependencies
        all_deps = valid_deps_targets

        # Add Library target
        if should_compile_lib:
            lib_name = f"{name}Lib"
            is_static = lib_config.get("static", True)
            lib_type = "STATIC" if is_static else "SHARED"
            cmake_content.append(f"add_library({lib_name} {lib_type}")
            for src in sources:
                cmake_content.append(f"    {src}")
            cmake_content.append(")")
            
            if include_dirs:
                cmake_content.append(f"target_include_directories({lib_name} PUBLIC")
                for idir in include_dirs:
                    cmake_content.append(f"    $<BUILD_INTERFACE:{idir}>")
                    cmake_content.append(f"    $<INSTALL_INTERFACE:include>")
                cmake_content.append(")")
            
            if valid_deps_targets:
                cmake_content.append(f"target_link_libraries({lib_name} PRIVATE")
                for dep in valid_deps_targets:
                    cmake_content.append(f"    {dep}")
                cmake_content.append(")")
            
            # Export and Install logic
            install_dir = lib_config.get("install_dir")
            if install_dir:
                abs_install_dir = os.path.abspath(os.path.join(project_dir, install_dir))
                cmake_content.append("")
                cmake_content.append(f"install(TARGETS {lib_name} EXPORT {name}Targets")
                cmake_content.append(f"    DESTINATION lib)")
                
                # Export headers
                export_headers = lib_config.get("export_headers", [])
                if export_headers:
                    cmake_content.append(f"install(FILES")
                    for header in export_headers:
                        cmake_content.append(f"    {header}")
                    cmake_content.append(f"    DESTINATION include)")

                cmake_content.append(f"install(EXPORT {name}Targets")
                cmake_content.append(f"    FILE {name}Targets.cmake")
                cmake_content.append(f"    NAMESPACE {name}::")
                cmake_content.append(f"    DESTINATION lib/cmake/{name})")
                
                # Generate Config file
                config_content = [
                    f"include(${{CMAKE_CURRENT_LIST_DIR}}/{name}Targets.cmake)",
                    f"set({name}_VERSION {version})"
                ]
                config_path = os.path.join(project_dir, f"{name}Config.cmake")
                with open(config_path, 'w') as f:
                    f.write("\n".join(config_content))
                
                cmake_content.append(f"install(FILES {name}Config.cmake")
                cmake_content.append(f"    DESTINATION lib/cmake/{name})")
            cmake_content.append("")

        # Add Executable target
        if should_compile_exec:
            entry_file = exec_config.get("entry_file")
            if not entry_file:
                print(f"Error: Executable enabled for {name} but no entry_file specified.")
                sys.exit(1)
            
            cmake_content.append(f"add_executable({name}")
            cmake_content.append(f"    {entry_file}")
            if not should_compile_lib:
                for src in sources:
                    if src != entry_file: 
                        cmake_content.append(f"    {src}")
            cmake_content.append(")")

            if include_dirs:
                cmake_content.append(f"target_include_directories({name} PRIVATE")
                for idir in include_dirs:
                    cmake_content.append(f"    {idir}")
                cmake_content.append(")")

            cmake_content.append(f"target_link_libraries({name} PRIVATE")
            if should_compile_lib:
                cmake_content.append(f"    {name}Lib")
            for dep in all_deps:
                cmake_content.append(f"    {dep}")
            cmake_content.append(")")
            cmake_content.append("")

        cmake_path = os.path.join(project_dir, "CMakeLists.txt")
        with open(cmake_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(cmake_content))
        
        print(f"Generated {cmake_path}")
        return primary_target

    def process_solution(self, solution_json_path):
        solution_json_path = os.path.abspath(solution_json_path)
        solution_dir = os.path.dirname(solution_json_path)
        with open(solution_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        name = data["name"]
        projects_dirs = data.get("projects", [])
        
        # When processing a solution, we act as the Root.
        # We manually collect all projects and generate their CMakeLists, 
        # then add them to the root CMakeLists.
        # Note: The Projects themselves will have `if(NOT TARGET ...)` guards, 
        # so we can just add the top-level projects, and they will add their deps if needed.
        # OR we can add everything in the solution.
        
        # Logic:
        # A Project.json might be in a subdir.
        # We process all listed projects.
        
        root_cmake = [
            f"cmake_minimum_required(VERSION 3.10)",
            f"project({name})",
            "",
            "set(CMAKE_CXX_STANDARD 17)",
            "set(CMAKE_CXX_STANDARD_REQUIRED ON)",
            ""
        ]

        for p_dir in projects_dirs:
            abs_p_dir = os.path.abspath(os.path.join(solution_dir, p_dir))
            # Process project to generate its CMakeLists.txt
            # We don't need the return target name here for the root CMake, 
            # we just need to add_subdirectory it.
            self.process_project(abs_p_dir, is_root=False) # is_root=False because solution is root
            root_cmake.append(f"add_subdirectory(\"{abs_p_dir.replace('\\', '/')}\" \"${{CMAKE_BINARY_DIR}}/{p_dir}\")")
        
        root_cmake_path = os.path.join(solution_dir, "CMakeLists.txt")
        with open(root_cmake_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(root_cmake))
        
        print(f"Generated root {root_cmake_path}")

def main():
    parser = argparse.ArgumentParser(description="Generate CMakeLists.txt from JSON configurations.")
    parser.add_argument("input", nargs="?", help="Path to Solution.json, Project.json, or directory containing them", default=None)
    args = parser.parse_args()

    if args.input is None:
        # Default lookup order: Solution.json then Project.json in current directory
        if os.path.exists("Solution.json"):
            args.input = "Solution.json"
        elif os.path.exists("Project.json"):
            args.input = "Project.json"
        else:
            print("Error: No input specified and neither Solution.json nor Project.json found in current directory.")
            sys.exit(1)

    if not os.path.exists(args.input):
        print(f"Error: Input {args.input} not found.")
        sys.exit(1)

    input_path = os.path.abspath(args.input)
    
    if os.path.isdir(input_path):
        json_path = os.path.join(input_path, "Project.json")
        is_solution = False
    else:
        json_path = input_path
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        is_solution = "projects" in data
    
    root_dir = os.path.dirname(json_path)
    # Search for 3rdparty up the tree
    search_dir = root_dir
    while search_dir != os.path.dirname(search_dir):
        if os.path.exists(os.path.join(search_dir, "3rdparty")):
            root_dir = search_dir
            break
        search_dir = os.path.dirname(search_dir)

    generator = CMakeGenerator(root_dir)
    if is_solution:
        generator.process_solution(json_path)
    else:
        generator.process_project(os.path.dirname(json_path), is_root=True)

if __name__ == "__main__":
    main()
