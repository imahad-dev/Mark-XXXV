import sys
from PyQt6.QtWidgets import QApplication
from ui_engine.hologram import DraggableHologramWindow

def main():
    app = QApplication(sys.argv)
    
    hud_window = DraggableHologramWindow()
    hud_window.show()
    
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
