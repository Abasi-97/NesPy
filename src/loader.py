class ROM:
    def __init__(self, filepath: str):
        with open(filepath, "rb") as f:
            header = f.read(16)
            
            # 1. Verify iNES file signature ('N', 'E', 'S', 0x1A)
            if header[:4] != b'NES\x1a':
                raise ValueError("Invalid .nes file format!")
            
            # after the signature check
            self.mirroring = header[6] & 1          # 0 = horizontal, 1 = vertical
            if header[6] & 4:
                f.read(512)                         # skip trainer if present

            prg_rom_size = header[4] * 16384
        
            # 2. Extract sizes
            prg_rom_size = header[4] * 16384  # 16KB per unit
            chr_rom_size = header[5] * 8192   # 8KB per unit
            
            # 3. Read ROM blocks
            self.prg_rom = f.read(prg_rom_size)
            self.chr_rom = f.read(chr_rom_size) if chr_rom_size > 0 else b''
            
            # 4. Determine and store Mapper ID
            self.mapper_id = (header[7] & 0xF0) | (header[6] >> 4)
            
            print(f"[ROM Loader] Loaded successfully:")
            print(f" - PRG-ROM: {len(self.prg_rom) // 1024} KB")
            print(f" - CHR-ROM: {len(self.chr_rom) // 1024} KB" if chr_rom_size > 0 else " - CHR-ROM: None")
            print(f" - Mapper: {self.mapper_id}")