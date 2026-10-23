"""重新打包单文件桌面版 exe。

先安装依赖：
    python -m pip install pyinstaller pywebview

然后：
    python build_exe.py

产物：dist/学习通刷课.exe（原生窗口，无控制台）
"""

import os

import PyInstaller.__main__

BASE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    PyInstaller.__main__.run([
        os.path.join(BASE, "desktop.py"),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "学习通刷课",
        "--icon", os.path.join(BASE, "icon.ico"),
        "--add-data", os.path.join(BASE, "web") + os.pathsep + "web",
        "--add-data", os.path.join(BASE, "icon.ico") + os.pathsep + ".",
        "--add-data", os.path.join(BASE, "font_map_table.json") + os.pathsep + ".",
        "--add-data", os.path.join(BASE, "LICENSE") + os.pathsep + ".",
        "--add-data", os.path.join(BASE, "NOTICE") + os.pathsep + ".",
        "--distpath", os.path.join(BASE, "dist"),
        "--workpath", os.path.join(BASE, "build"),
        "--specpath", os.path.join(BASE, "build"),
    ])


if __name__ == "__main__":
    main()
