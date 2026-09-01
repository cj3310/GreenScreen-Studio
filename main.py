"""绿幕抠图工作台 — 程序入口（跨机运行容错版）。

改进点（针对打包到其它电脑运行）：
1. 打包后显式设置 Qt 平台插件路径，避免找不到 qwindows.dll 导致窗口起不来。
2. 全局异常捕获 + 不依赖 Qt 的错误弹窗（ctypes），任何加载/运行错误都会弹出并写 error.log。
"""

import os
import sys
import traceback

# 打包后为 _internal 目录；源码运行时为脚本所在目录
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _setup_qt_plugin_path():
    """显式指定 Qt 平台插件目录，兼容不同 PyInstaller 版本/路径布局。"""
    candidates = [
        os.path.join(BASE, "PySide6", "plugins", "platforms"),
        os.path.join(BASE, "PySide6", "Qt", "plugins", "platforms"),
        os.path.join(BASE, "_internal", "PySide6", "plugins", "platforms"),
        os.path.join(BASE, "_internal", "PySide6", "Qt", "plugins", "platforms"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = c
            # 同时把插件目录加入 DLL 搜索路径，保证 msvcp140 等能被找到
            try:
                os.add_dll_directory(c)
            except Exception:
                pass
            return c
    return None


def _log_and_popup(title, text):
    """不依赖 Qt 的错误弹窗 + 写日志文件（即使 Qt 没起来也能提示）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x10)
    except Exception:
        pass
    try:
        out_dir = (
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.getcwd()
        )
        with open(os.path.join(out_dir, "error.log"), "w", encoding="utf-8") as f:
            f.write("=== GreenScreenStudio 错误日志 ===\n")
            f.write(text)
    except Exception:
        pass


def main():
    plugin_dir = _setup_qt_plugin_path()

    # 依赖加载阶段失败也要能被捕获并提示
    try:
        from PySide6.QtWidgets import QApplication
        from app.main_window import GreenScreenStudio
    except Exception:
        _log_and_popup("启动失败（依赖加载）", traceback.format_exc())
        return

    # 运行期未捕获异常 -> 弹窗 + 日志
    def _excepthook(etype, exc, tb):
        _log_and_popup(
            "程序运行异常", "".join(traceback.format_exception(etype, exc, tb))
        )

    sys.excepthook = _excepthook

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("GreenScreen Studio")
        win = GreenScreenStudio()
        win.show()
        sys.exit(app.exec())
    except Exception:
        _log_and_popup(
            "GUI 启动失败",
            "无法创建主窗口。常见原因：缺少 VC++ 运行库、Qt 平台插件未找到、"
            "或显示器/DPI 配置异常。\n\n" + traceback.format_exc(),
        )


if __name__ == "__main__":
    main()
