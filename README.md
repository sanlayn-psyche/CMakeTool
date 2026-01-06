# CMake Generator Tool (gen_cmake.py)

这是一个用于从 JSON 配置文件生成 `CMakeLists.txt` 的 Python 工具。旨在简化 C++ 项目的构建配置，特别是对于第三方库的依赖管理。

## 依赖 (Requirements)

*   Python 3.x

## 用法 (Usage)

### 1. 生成单个项目

在项目目录下运行，或指定 `Project.json` 路径：

```bash
python gen_cmake.py [path/to/Project.json]
```

如果当前目录下有 `Project.json`，可以直接运行：

```bash
python gen_cmake.py
```

### 2. 生成解决方案 (多项目)

指定 `Solution.json` 路径：

```bash
python gen_cmake.py path/to/Solution.json
```

## 配置文件 (Configuration)

### Project.json (项目配置)

```json
{
    "name": "MyProject",
    "version": "1.0.0",
    "source_dirs": ["src"],        // 递归扫描源文件
    "include_dirs": ["include"],   // 添加 include 路径 (非递归)
    "third_party_deps": ["rapidcsv", "fmt"], // 3rdparty 下的库名
    "internal_deps": [],           // 内部项目依赖 (相对路径)
    "executable": {
        "compile": true,
        "entry_file": "src/main.cpp"
    },
    "library": {
        "compile": false,
        "static": true,
        "install_dir": "install"   // 启用 install/export 功能
    }
}
```

### Solution.json (解决方案配置)

```json
{
    "name": "MySolution",
    "projects": [
        "projects/LibA",
        "projects/AppB"
    ]
}
```

## 第三方库导入逻辑 (3rdparty Import Logic)

工具会自动在 `root/3rdparty` 或项目下的 `3rdparty` 目录查找依赖。查找优先级如下：

1.  **Module Mode**: 查找 `Find<Name>.cmake` -> 使用 `find_package` (System Module)。
2.  **Config Mode**: 递归查找 `*Config.cmake` -> 使用 `find_package` (Config Mode)。
3.  **Source Mode**: 查找 `CMakeLists.txt` -> 使用 `add_subdirectory` (源码编译)。
    *   **注意**: 源码方式引入的库会自动屏蔽 export 接口 (`$<BUILD_INTERFACE:...>`)，避免安装时报错。
4.  **Project Mode**: 查找 `Project.json` -> 递归生成 CMakeLists 并 `add_subdirectory`。

## 库安装指南 (Library Installation)

本工具依赖于目录结构来查找第三方库。请按照以下方式“安装”您的依赖库：

### 1. 放置位置
将第三方库放置在项目根目录或子项目的 `3rdparty` 文件夹中。
例如：`D:/Projects/MySolution/3rdparty/rapidcsv`

### 2. 支持的形式
确保库的目录结构满足以下任一条件，工具才能自动识别：

*   **源码库 (推荐)**: 包含 `CMakeLists.txt`。
    *   工具会使用 `add_subdirectory` 引入。
    *   会自动处理导出问题 (Build Interface)。
*   **预编译/标准库**: 包含 `*Config.cmake` 或 `Find<Name>.cmake`。
    *   工具会使用 `find_package` 引入。
    *   适用于 vcpkg 安装的库或系统库（需将路径放入 3rdparty 或配置环境变量）。

### 3. 项目引用
在 `Project.json` 中只需把文件夹名字加入 `third_party_deps` 列表即可：
```json
"third_party_deps": ["rapidcsv"]
```
