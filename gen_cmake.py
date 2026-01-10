import json
import os
import glob
import sys
import argparse
import re
import platform

class CMakeGenerator:
    @staticmethod
    def load_json(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Remove trailing commas in arrays and objects
            content = re.sub(r',\s*([\]}])', r'\1', content)
            return json.loads(content)

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
            return "PROJECT", tp_path, os.path.basename(tp_path)

        # 1. Module Mode: Look for Find<Name>.cmake
        tp_name = os.path.basename(tp_path)
        if os.path.exists(os.path.join(tp_path, f"Find{tp_name}.cmake")):
             return "MODULE", tp_path, tp_name
        
        # Also check for case-insensitive Find matches or common variants? 
        # For now, stick to direct.
        
        # 2. Config Mode: Look for *Config.cmake recursively
        tp_name = os.path.basename(tp_path)
        tp_name_lower = tp_name.lower()
        
        # Clean the name for better matching (e.g. vtk-9.4.1-x64 -> vtk)
        clean_name = re.sub(r'[-_]?(?:vc|gcc|clang|win|linux|mac)?\d+(?:[\.-]\d+)*.*$', '', tp_name, flags=re.IGNORECASE)
        if not clean_name:
            clean_name = tp_name
        clean_name_lower = clean_name.lower()

        parent_name = os.path.basename(os.path.dirname(tp_path))
        parent_name_lower = parent_name.lower()
        is_version_dir = any(c.isdigit() for c in tp_name) and '.' in tp_name

        candidates = []
        ignore_configs = ["ctestconfig.cmake", "cpackconfig.cmake", "ctestcustom.cmake"]
        
        for root, dirs, files in os.walk(tp_path):
            # Check files
            for f in files:
                f_lower = f.lower()
                if f_lower in ignore_configs:
                    continue
                
                if f_lower.endswith("config.cmake") or f_lower.endswith("-config.cmake"):
                    current_pkg_name = f[:f_lower.find("config.cmake")].rstrip("-")
                    if not current_pkg_name:
                         current_pkg_name = tp_name
                    
                    # Store candidate with a score
                    score = 0
                    if f_lower == f"{clean_name_lower}config.cmake" or f_lower == f"{clean_name_lower}-config.cmake":
                        score = 10
                    elif f_lower == f"{tp_name_lower}config.cmake" or f_lower == f"{tp_name_lower}-config.cmake":
                        score = 9
                    elif clean_name_lower in f_lower:
                        score = 7
                    elif tp_name_lower in f_lower:
                        score = 5
                    elif is_version_dir and (f_lower == f"{parent_name_lower}config.cmake" or parent_name_lower in f_lower):
                        score = 8
                    
                    candidates.append({"name": current_pkg_name, "root": root, "score": score})
            
            # optimization: don't go too deep or into build dirs
            depth = root[len(tp_path):].count(os.sep)
            if depth > 5:
                del dirs[:] 
        
        if candidates:
            # Pick best candidate
            best = max(candidates, key=lambda x: x["score"])
            return "CONFIG", best["root"], best["name"]
        
        # 3. CMake Source: Look for CMakeLists.txt
        if os.path.exists(os.path.join(tp_path, "CMakeLists.txt")):
            return "SOURCE", tp_path, tp_name
        
        # 4. Root Mode: Look for include/ and lib/
        if os.path.isdir(os.path.join(tp_path, "include")) and os.path.isdir(os.path.join(tp_path, "lib")):
            # Heuristic: Clean the name (already done above but scope issue if we rely on it)
            # Re-calculate clean name just in case checks above didn't pass or we are in a separate logic block,
            # but ideally we should reuse logic. Here we just use the regex again.
            clean_name_root = re.sub(r'[-_]?(?:vc|gcc|clang|win|linux|mac)?\d+(?:[\.-]\d+)*.*$', '', tp_name, flags=re.IGNORECASE)
            if not clean_name_root:
                clean_name_root = tp_name
            return "ROOT", tp_path, clean_name_root
        
        return "UNKNOWN", None, tp_name

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

        method, location, pkg_name = self.check_import_method(resolved_path)
        return resolved_path, method, location, pkg_name

    def process_project(self, project_dir, is_root=False):
        project_dir = os.path.abspath(project_dir)
        project_json_path = os.path.join(project_dir, "Project.json")
        
        if not os.path.exists(project_json_path):
            print(f"Error: Project.json not found in {project_dir}")
            sys.exit(1)

        if project_json_path in self.processed_projects:
            return self.processed_projects[project_json_path]

        data = self.load_json(project_json_path)

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
        
        # We process to find deps first, then we can cache fully.
        # But wait, we need to return valid data.
        # Ideally we process, THEN cache.
        # For now, recursive check handles circles?
        # self.processed_projects[project_json_path] = (primary_target, []) # Placeholder logic if needed?
        # Actually, let's just proceed and cache at end or handle recursion loop detection separately if needed.
        # For now, just continue.

        # Combine internal and third-party dependencies
        raw_deps = data.get("dependencies", [])
        
        # Pre-process dependencies to handle wildcards
        all_deps = []
        for raw_dep in raw_deps:
            # Expand env vars first to check path
            dep_expanded = self.expand_env_vars(raw_dep)
            norm_dep = os.path.normpath(dep_expanded)
            
            if norm_dep.endswith("*"):
                # Wildcard detected
                base_dir = os.path.dirname(norm_dep)
                if os.path.isdir(base_dir):
                    # Found directory, list all subdirectories
                    found_any = False
                    for entry in os.listdir(base_dir):
                        full_path = os.path.join(base_dir, entry)
                        if os.path.isdir(full_path):
                            all_deps.append(full_path)
                            found_any = True
                    if not found_any:
                        print(f"Warning: No subdirectories found in wildcard path '{dep_expanded}'")
                else:
                    print(f"Warning: Wildcard base directory '{base_dir}' not found for dependency '{raw_dep}'")
            else:
                # Normal dependency
                all_deps.append(dep_expanded)

        valid_deps_targets = []
        dep_cmake_cmds = []
        dll_copy_dirs = []

        for dep in all_deps:
            # Note: dep is already expanded env vars, but self.expand_env_vars is idempotent usually.
            # We keep the logic consistent.
            dep = self.expand_env_vars(dep)
            dep = self.expand_env_vars(dep)
            
            # Normalize path and remove trailing slashes
            dep = os.path.normpath(dep)
            
            abs_dep_path, method, location, pkg_name = self.resolve_dependency(dep, project_dir)
            
            if not abs_dep_path:
                 print(f"Error: Dependency '{dep}' not found.")
                 sys.exit(1)
            
            # Skip if the dependency is the project itself
            if abs_dep_path == project_dir:
                print(f"Note: Skipping self-referential dependency: {dep}")
                continue
            
            dep_name = pkg_name or os.path.basename(abs_dep_path)

            # DLL Auto-Discovery Logic
            potential_bin_dirs = [
                os.path.join(abs_dep_path, "bin"), # Standard
                os.path.join(abs_dep_path, "win64", "vc14", "bin"), # OCCT Standard
                os.path.join(abs_dep_path, "lib") # Fallback
            ]
            for pbd in potential_bin_dirs:
                if os.path.exists(pbd) and glob.glob(os.path.join(pbd, "*.dll")):
                    dll_copy_dirs.append(pbd.replace('\\', '/'))
                    break
            # If name is still a version string, try harder? 
            # But pkg_name should have caught it for CONFIG mode.

            escaped_loc = location.replace('\\', '/') if location else ""
            escaped_dep_path = abs_dep_path.replace('\\', '/')

            if method == "MODULE":
                # Find<Name>.cmake found
                dep_cmake_cmds.append(f"list(APPEND CMAKE_MODULE_PATH \"{escaped_loc}\")")
                dep_cmake_cmds.append(f"list(APPEND CMAKE_PREFIX_PATH \"{escaped_dep_path}\")")
                dep_cmake_cmds.append(f"set({dep_name}_ROOT \"{escaped_dep_path}\")")
                dep_cmake_cmds.append(f"find_package({dep_name} REQUIRED)")
                valid_deps_targets.append(dep_name)
                
            elif method == "CONFIG":
                # Config file found
                dep_cmake_cmds.append(f"list(APPEND CMAKE_PREFIX_PATH \"{escaped_dep_path}\")")
                dep_cmake_cmds.append(f"list(APPEND CMAKE_PREFIX_PATH \"{escaped_loc}\")")
                dep_cmake_cmds.append(f"set({dep_name}_ROOT \"{escaped_dep_path}\")")
                dep_cmake_cmds.append(f"find_package({dep_name} REQUIRED)")
                # Use standard variable or target name
                # If ${dep_name}_LIBRARIES is defined, use it.
                # Else check if checks if a target with ${dep_name} exists.
                # If neither, link nothing (avoids LNK1104 for non-target package names).
                link_name = f"$<IF:$<BOOL:${{{dep_name}_LIBRARIES}}>,${{{dep_name}_LIBRARIES}},$<TARGET_NAME_IF_EXISTS:{dep_name}>>"
                valid_deps_targets.append(link_name)
                
                # For include dirs, since we can't easily add them to target_include_directories 
                # after the fact in this script's structure without major refactor, 
                # we'll use include_directories (global) or add to include_dirs list.
                dep_cmake_cmds.append(f"if({dep_name}_INCLUDE_DIRS)")
                dep_cmake_cmds.append(f"    include_directories(${{{dep_name}_INCLUDE_DIRS}})")
                dep_cmake_cmds.append(f"elseif({dep_name}_INCLUDE_DIR)")
                dep_cmake_cmds.append(f"    include_directories(${{{dep_name}_INCLUDE_DIR}})")
                dep_cmake_cmds.append(f"endif()")
                
            elif method == "SOURCE":
                # CMakeLists.txt found
                dep_cmake_cmds.append(f"if(NOT TARGET {dep_name})")
                dep_cmake_cmds.append(f"    add_subdirectory(\"{escaped_dep_path}\" \"${{CMAKE_BINARY_DIR}}/deps/{dep_name}\")")
                dep_cmake_cmds.append(f"endif()")
                valid_deps_targets.append(dep_name)
                
            elif method == "PROJECT":
                # Project.json found. Recursively generate it!
                dep_target, child_dll_dirs = self.process_project(abs_dep_path, is_root=False)
                
                # Bubble up transitive DLLs
                dll_copy_dirs.extend(child_dll_dirs)

                # Now add it
                dep_cmake_cmds.append(f"if(NOT TARGET {dep_target})")
                dep_cmake_cmds.append(f"    add_subdirectory(\"{escaped_dep_path}\" \"${{CMAKE_BINARY_DIR}}/deps/{dep_name}\")")
                dep_cmake_cmds.append(f"endif()")
                if dep_target:
                    valid_deps_targets.append(dep_target)
            elif method == "ROOT":
                # Raw package found (include/ + lib/) without Config/Find module.
                # Strictly import headers and link all found libraries.
                
                # Add include directory
                dep_cmake_cmds.append(f"include_directories(\"{escaped_dep_path}/include\")")
                
                # Find libraries
                # We assume Windows (.lib) for now based on environment. 
                # For cross-platform, we would add .a and .so
                lib_var = f"{dep_name}_LIBRARIES"
                dep_cmake_cmds.append(f"file(GLOB {lib_var} \"{escaped_dep_path}/lib/*.lib\")")
                
                # Add to targets
                valid_deps_targets.append(f"${{{lib_var}}}")
            else:
                print(f"Warning: Could not determine how to import dependency '{dep_name}' at {abs_dep_path}. Skipping.")
                continue

        sources = self.collect_source_files(project_dir, data.get("source_dirs", []))
        
        cmake_content = [
            f"cmake_minimum_required(VERSION 3.12)",
            f"project({name} VERSION {version})",
            "",
            # Enable compatibility with older projects in CMake 4.0
            "if(POLICY CMP0000)",
            "    set(CMAKE_POLICY_VERSION_MINIMUM 3.5)",
            "endif()",
            "",
            "if(POLICY CMP0079)",
            "    cmake_policy(SET CMP0079 NEW)",
            "endif()",
            "",
            f"add_definitions(-DRootPath=\"{project_dir.replace('\\', '/')}\")",
            ""
        ]

        # Use C++17 by default for consistency
        # C++ Standard
        cpp_std = data.get("cpp_standard", 17)
        cmake_content.append(f"set(CMAKE_CXX_STANDARD {cpp_std})")
        cmake_content.append(f"set(CMAKE_CXX_STANDARD_REQUIRED ON)")
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
            
            # Idempotency guard for library
            cmake_content.append(f"if(NOT TARGET {lib_name})")
            cmake_content.append(f"    add_library({lib_name} {lib_type}")
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
            
            cmake_content.append("endif()")
            cmake_content.append("")

        # Add Executable target
        if should_compile_exec:
            entry_file = exec_config.get("entry_file")
            if not entry_file:
                print(f"Error: Executable enabled for {name} but no entry_file specified.")
                sys.exit(1)
            
            cmake_content.append(f"if(NOT TARGET {name})")
            cmake_content.append(f"    add_executable({name}")
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

            # Add Post-Build Auto-Copy for DLLs
            if dll_copy_dirs:
                cmake_content.append("")
                cmake_content.append(f"# Auto-copy Dependency DLLs to Output Directory")
                # Deduplicate directories
                unique_dll_dirs = list(set(dll_copy_dirs))
                for i, d in enumerate(unique_dll_dirs):
                    var_name = f"DEP_DLLS_{i}"
                    cmake_content.append(f"file(GLOB {var_name} \"{d}/*.dll\")")
                    cmake_content.append(f"foreach(dll ${{{var_name}}})")
                    cmake_content.append(f"    add_custom_command(TARGET {name} POST_BUILD")
                    cmake_content.append(f"        COMMAND ${{CMAKE_COMMAND}} -E copy_if_different")
                    cmake_content.append(f"        \"${{dll}}\"")
                    cmake_content.append(f"        \"$<TARGET_FILE_DIR:{name}>\"")
                    cmake_content.append(f"    )")
                    cmake_content.append(f"endforeach()")

            cmake_content.append("endif()") # Close if(NOT TARGET {name})
            cmake_content.append("")

        cmake_path = os.path.join(project_dir, "CMakeLists.txt")
        with open(cmake_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(cmake_content))
        
        print(f"Generated {cmake_path}")

        # Cache result
        self.processed_projects[project_json_path] = (primary_target, dll_copy_dirs)
        
        return primary_target, dll_copy_dirs

    def process_solution(self, solution_json_path):
        solution_json_path = os.path.abspath(solution_json_path)
        solution_dir = os.path.dirname(solution_json_path)
        data = self.load_json(solution_json_path)

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
            f"cmake_minimum_required(VERSION 3.12)",
            f"project({name})",
            "",
            "if(POLICY CMP0000)",
            "    set(CMAKE_POLICY_VERSION_MINIMUM 3.5)",
            "endif()",
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
        data = CMakeGenerator.load_json(json_path)
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
