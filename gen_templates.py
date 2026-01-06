import json
import os
import argparse

def generate_solution_template(output_path):
    if os.path.exists(output_path):
        print(f"Skipping: {output_path} already exists.")
        return
    template = {
        "name": "MySolution",
        "projects": [
            "projects/MyProject"
        ]
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(template, f, indent=4)
    print(f"Generated solution at: {output_path}")

def generate_project_template(output_path):
    if os.path.exists(output_path):
        print(f"Skipping: {output_path} already exists.")
        return
    template = {
        "name": "MyProject",
        "version": "1.0.0",
        "source_dirs": ["src"],
        "include_dirs": ["include"],
        "third_party_deps": [],
        "internal_deps": [],
        "executable": {
            "compile": True,
            "entry_file": "src/main.cpp"
        },
        "library": {
            "compile": False,
            "static": True,
            "export_headers": ["include/MyProject/api.h"],
            "install_dir": "install/MyProject"
        }
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(template, f, indent=4)
    print(f"Generated project at: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Generate CMake JSON configurations.")
    parser.add_argument("-t", "--type", choices=["solution", "project", "both"], help="Type of file to generate", default="both")
    parser.add_argument("-o", "--output", help="Output file path (ignored if type is 'both')", default=None)

    args = parser.parse_args()

    if args.type == "solution":
        output = args.output if args.output else "Solution.json"
        generate_solution_template(output)
    elif args.type == "project":
        output = args.output if args.output else "Project.json"
        generate_project_template(output)
    else:
        generate_solution_template("Solution.json")
        generate_project_template("Project.json")

if __name__ == "__main__":
    main()
