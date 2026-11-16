from src.Interpreter import CPU
from emu import Emu


class OpcodeHandler:
    def __init__(self) -> None:
        self.emu = Emu()
        self.cpu = CPU()
    
    