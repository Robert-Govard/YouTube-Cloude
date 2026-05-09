"""Точка входа: GUI или CLI в зависимости от аргументов."""

import sys


def main():
    # Если переданы аргументы — запускаем CLI (старый режим coder.py)
    if len(sys.argv) > 1:
        from coder import main as cli_main
        cli_main()
        return

    # Иначе — запускаем GUI
    from ui.app import App
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
