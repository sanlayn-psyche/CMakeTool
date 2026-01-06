import json
import os
import glob
import sys
import argparse

class CMakeGenerator:
    def __init__(self, root_dir):
        self.root_dir = os.path.abspath(root_dir)
        self.third_party_dir = os.path.join(self.root_dir, "3rdparty")
        self.processed_projects = {}

    def get_relative_path(self, target_path, base_path):
        return os.path.relpath(target_path, base_path).replace("\\", "/")

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
        
        # 4. Project JSON: Look for Project.json
        if os.path.exists(os.path.join(tp_path, "Project.json")):
            return "PROJECT", tp_path
            
        return "UNKNOWN", None

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

        # Resolve internal dependencies recursively
        internal_deps_targets = []
        internal_deps_dirs = []
        
        for dep_dir in data.get("internal_deps", []):
            abs_dep_dir = os.path.abspath(os.path.join(project_dir, dep_dir))
            dep_target = self.process_project(abs_dep_dir, is_root=False)
            if dep_target:
                internal_deps_targets.append(dep_target)
                internal_deps_dirs.append(abs_dep_dir)

        # Resolve third party dependencies
        # We need to determine HOW to import them: find_package or add_subdirectory
        third_party_targets = []
        
        # Lists to hold CMake commands to inject
        tp_cmake_cmds = []

        for tp in data.get("third_party_deps", []):
            # Resolve path
            abs_tp_path = os.path.abspath(os.path.join(project_dir, tp))
            if not os.path.isdir(abs_tp_path):
                abs_tp_path = os.path.abspath(os.path.join(self.third_party_dir, tp))
            
            if not os.path.isdir(abs_tp_path):
                 print(f"Error: Third party dependency '{tp}' not found.")
                 sys.exit(1)
            
            tp_name = os.path.basename(abs_tp_path)
            method, location = self.check_import_method(abs_tp_path)
            
            escaped_loc = location.replace('\\', '/') if location else ""
            escaped_tp_path = abs_tp_path.replace('\\', '/')

            if method == "MODULE":
                # Find<Name>.cmake found
                tp_cmake_cmds.append(f"list(APPEND CMAKE_MODULE_PATH \"{escaped_tp_path}\")")
                tp_cmake_cmds.append(f"find_package({tp_name} REQUIRED)")
                third_party_targets.append(tp_name) # Assuming target name matches package name
                
            elif method == "CONFIG":
                # Config file found
                tp_cmake_cmds.append(f"find_package({tp_name} REQUIRED PATHS \"{escaped_loc}\")")
                third_party_targets.append(tp_name) # Assuming target name matches package
                
            elif method == "SOURCE":
                # CMakeLists.txt found
                # Add subdirectory. Use binary dir to support out-of-tree
                tp_cmake_cmds.append(f"add_subdirectory(\"{escaped_tp_path}\" \"${{CMAKE_BINARY_DIR}}/3rdparty/{tp_name}\")")
                third_party_targets.append(tp_name) # Assuming target name matches dir
                
            elif method == "PROJECT":
                # Project.json found. Recursively generate it!
                # Treat it similar to internal dep but it lives in 3rdparty
                # We need to process it to generate its CMakeLists.txt
                # BUT, process_project returns the target name.
                tp_target = self.process_project(abs_tp_path, is_root=False)
                # Now add it
                tp_cmake_cmds.append(f"add_subdirectory(\"{escaped_tp_path}\" \"${{CMAKE_BINARY_DIR}}/3rdparty/{tp_name}\")")
                if tp_target:
                    third_party_targets.append(tp_target)
            else:
                print(f"Error: Could not determine how to import 3rdparty dependency '{tp_name}' at {abs_tp_path}")
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
        
        if internal_deps_dirs:
            for idep_dir in internal_deps_dirs:
                rel_path = self.get_relative_path(idep_dir, project_dir) # Use relative if possible? No, abs is safer for out-of-tree.
                # Actually, internal deps are usually relative.
                # Let's use absolute path for add_subdirectory to be safe.
                abs_idep = idep_dir.replace('\\', '/')
                idep_name = os.path.basename(idep_dir) # simple name for binary dir
                
                # We need a unique binary directory.
                # If we are root, we can just add them. 
                # If we are added by someone else, they might have added them.
                # Guarding with NOT TARGET is tricky if we don't know the exact target name beforehand (though we do have it from process_project).
                # But we have `internal_deps_targets` corresponding to dirs.
                # Let's just add the commands. CMake handles multiple add_subdirectory calls to the same dir gracefully? 
                # No, it errors if added twice to same binary dir usually, or defines targets twice.
                # We'll use: if(NOT TARGET TargetName) add_subdirectory(...) endif()
                
                # NOTE: We can't easily know the TargetName BEFORE processing, but we just processed it!
                # `internal_deps_targets` has the names.
                # Wait, this loop needs to match targets to dirs.
                # Let's iterate together.
                pass 

        # We will add Third Party commands first
        if tp_cmake_cmds:
            cmake_content.extend(tp_cmake_cmds)
            cmake_content.append("")

        # Now Internal Deps
        # We only add_subdirectory if we are the ROOT project being processed or if we want to ensure standalone builds work.
        # The user said "All ... included in the FINAL solution".
        # If this CMakeLists.txt is the entry point, it must add them.
        # If it's a child, it shouldn't add them if the parent already did.
        # Using `if(NOT TARGET ...)` is the standard way to handle diamond dependencies / shared deps.
        for idx, idep_dir in enumerate(internal_deps_dirs):
            target_name = internal_deps_targets[idx]
            abs_idep = idep_dir.replace('\\', '/')
            # Binary dir needs to be unique relative to build.
            # We can use CMAKE_BINARY_DIR/internal/TargetName
            cmake_content.append(f"if(NOT TARGET {target_name})")
            cmake_content.append(f"    add_subdirectory(\"{abs_idep}\" \"${{CMAKE_BINARY_DIR}}/internal/{target_name}\")")
            cmake_content.append(f"endif()")
        cmake_content.append("")

        # Include dirs
        raw_include_dirs = data.get("include_dirs", [])
        include_dirs = []
        for idir in raw_include_dirs:
            # Resolve to absolute path immediately
            abs_idir = os.path.abspath(os.path.join(project_dir, idir)).replace('\\', '/')
            include_dirs.append(abs_idir)

        # Combine linker dependencies
        all_deps = internal_deps_targets + third_party_targets

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
            
            if internal_deps_targets or third_party_targets:
                cmake_content.append(f"target_link_libraries({lib_name} PRIVATE")
                for dep in internal_deps_targets:
                    cmake_content.append(f"    {dep}")
                for dep in third_party_targets:
                    cmake_content.append(f"    $<BUILD_INTERFACE:{dep}>")
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
