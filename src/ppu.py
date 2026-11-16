NES_PALETTE = [
    (84, 84, 84), (0, 30, 116), (8, 16, 144), (48, 0, 136), (68, 0, 100), (92, 0, 48), (84, 4, 0), (60, 24, 0),
    (32, 42, 0), (8, 58, 0), (0, 64, 0), (0, 60, 0), (0, 50, 60), (0, 0, 0), (0, 0, 0), (0, 0, 0),
    (152, 150, 152), (8, 76, 196), (48, 50, 236), (92, 30, 228), (136, 20, 176), (160, 20, 100), (152, 34, 32), (120, 60, 0),
    (84, 90, 0), (40, 114, 0), (8, 124, 0), (0, 118, 40), (0, 102, 120), (0, 0, 0), (0, 0, 0), (0, 0, 0),
    (236, 238, 236), (76, 154, 236), (120, 124, 236), (176, 98, 236), (228, 84, 236), (236, 88, 180), (236, 106, 100), (212, 136, 32),
    (160, 170, 0), (116, 196, 0), (76, 208, 32), (56, 204, 108), (56, 180, 204), (60, 60, 60), (0, 0, 0), (0, 0, 0),
    (236, 238, 236), (168, 204, 236), (188, 188, 236), (212, 178, 236), (236, 174, 236), (236, 174, 212), (236, 180, 176), (228, 196, 144),
    (204, 210, 120), (180, 222, 120), (168, 226, 144), (152, 226, 180), (160, 214, 228), (160, 162, 160), (0, 0, 0), (0, 0, 0),
]
PALETTE_BYTES = [bytes(c) for c in NES_PALETTE]

# One pattern-table row (two bitplane bytes) -> 8 colour indexes. Built lazily, 64K entries max.
_ROWS = [None] * 65536
# opaque-pixel mask for a row, indexed by (lo | hi)
_MASK_ROWS = [bytes([(v >> (7 - x)) & 1 for x in range(8)]) for v in range(256)]


def _row(lo, hi):
    k = (lo << 8) | hi
    r = _ROWS[k]
    if r is None:
        r = _ROWS[k] = tuple(((lo >> (7 - x)) & 1) | (((hi >> (7 - x)) & 1) << 1) for x in range(8))
    return r


class PPU:
    CYCLES_PER_FRAME = 29780
    VBLANK_START_CYCLE = 27394
    CYCLES_PER_LINE = 113.667

    def __init__(self, chr_rom=None, mirroring=0):
        self.registers = bytearray(8)
        self.vram = bytearray(2048)
        self.palette = bytearray(32)
        self.chr_rom = chr_rom or bytearray(8192)   # replaced by the mapper's CHR window when attached
        self.chr_writable = not chr_rom             # True for CHR-RAM
        self.chr_version = 0                        # bumped whenever pattern data changes
        # 0 = horizontal, 1 = vertical, 2 = one-screen (lower), 3 = one-screen (upper)
        self.mirroring = mirroring

        self.oam = bytearray(256)
        self.oam_addr = 0

        self.vram_address = 0          # "v": address used by $2007
        self.t = 0                     # "t": temporary address, also holds the scroll position
        self.fine_x = 0
        self.address_latch = 0
        self.vram_buffered_read = 0

        self.bg_mask = bytearray(256 * 240)         # per-pixel "background is opaque" for the last frame
        self._zero_mask = bytes(256 * 240)

        self.frame_cycle = 0
        self.vblank_active = False
        self.sprite0_hit = False
        self._s0_cache = None          # None = not computed this frame, -1 = never hits
        self.frame_counter = 0
        self._nmi_pending = False
        self.cpu = None
        self._sync = 0                 # cpu.cycles at the last tick, to estimate the scanline of a write

        # Scroll bands for the frame being drawn: (first_scanline, x_pixels, y_pixels at that scanline).
        # A new band is added whenever the game changes the scroll mid-frame (status bar splits).
        self._bands = [(0, 0, 0)]

        # All four nametables pre-drawn into one 512x480 map; the screen is cut out of it per scanline.
        self._map = bytearray(512 * 480 * 3)
        self._map_mask = bytearray(512 * 480)
        self._map_mv = memoryview(self._map)
        self._map_mask_mv = memoryview(self._map_mask)
        self._map_ctx = None
        self._map_colors = None
        self._nt_sigs = [None] * 4
        self._map_keys = [[-1] * 960 for _ in range(4)]
        self._tile_cache = {}

    # ---------------------------------------------------------------- timing

    def _enter_vblank(self):
        self.vblank_active = True
        self.frame_counter += 1
        return bool(self.registers[0] & 0x80)

    def tick(self, cpu_cycles: int) -> bool:
        frame = self.CYCLES_PER_FRAME
        vb = self.VBLANK_START_CYCLE
        prev = self.frame_cycle
        end = prev + cpu_cycles
        nmi = False

        while end >= frame:                      # crossed the end of a frame
            if prev < vb and self._enter_vblank():
                nmi = True
            self.vblank_active = False           # vblank ends at the pre-render line
            self.sprite0_hit = False
            self._s0_cache = None
            self._bands = [(0, self._x_px(), self._y_px())]   # scroll in effect at the top of the frame
            prev = 0
            end -= frame

        if prev < vb <= end and self._enter_vblank():
            nmi = True

        self.frame_cycle = end
        if self.cpu is not None:
            self._sync = self.cpu.cycles
        if self._nmi_pending:
            nmi = True
            self._nmi_pending = False
        return nmi

    # --------------------------------------------------------------- scrolling

    def _x_px(self):
        t = self.t
        return ((t >> 10) & 1) * 256 + (t & 0x1F) * 8 + self.fine_x

    def _y_px(self):
        t = self.t
        return ((t >> 11) & 1) * 240 + ((t >> 5) & 0x1F) * 8 + ((t >> 12) & 7)

    def _scanline_now(self):
        fc = self.frame_cycle
        if self.cpu is not None:
            fc += self.cpu.cycles - self._sync
        return int(fc / self.CYCLES_PER_LINE)

    def _scroll_event(self, x_changed, y_changed):
        """A scroll register was written. Horizontal changes apply from the next scanline; vertical
        ones mid-frame only happen through a $2006 write (as on hardware)."""
        line = self._scanline_now() + 1
        if line >= 240:
            return                      # vblank: picked up when the next frame starts
        bands = self._bands
        start, bx, by = bands[-1]
        if line < start:
            line = start
        x = self._x_px() if x_changed else bx
        y = self._y_px() if y_changed else by + (line - start)
        if start == line:
            bands[-1] = (line, x, y)
        else:
            bands.append((line, x, y))

    # --------------------------------------------------------- sprite 0 hit

    def _bg_opaque(self, x, y):
        band = self._bands[0]
        for b in self._bands:
            if b[0] <= y:
                band = b
        start, bx, by = band
        wx = (bx + x) % 512
        wy = (by + y - start) % 480
        nti = (wx >> 8) | ((wy // 240) << 1)
        rx = wx & 255
        ry = wy % 240
        off = self._nt_index(0x2000 + nti * 0x400)
        tile = self.vram[off + (ry >> 3) * 32 + (rx >> 3)]
        base = (0x1000 if self.registers[0] & 0x10 else 0) + tile * 16 + (ry & 7)
        return ((self.chr_rom[base] | self.chr_rom[base + 8]) >> (7 - (rx & 7))) & 1

    def _find_sprite0(self):
        """CPU-cycle offset (from frame start) at which sprite 0 first overlaps an opaque bg pixel."""
        oam = self.oam
        ctrl = self.registers[0]
        y0 = oam[0] + 1
        tile = oam[1]
        attr = oam[2]
        x0 = oam[3]
        tall = bool(ctrl & 0x20)
        height = 16 if tall else 8
        chr_data = self.chr_rom
        for row in range(height):
            sy = y0 + row
            if sy >= 240:
                break
            r = (height - 1 - row) if attr & 0x80 else row
            if tall:
                a = (tile & 1) * 0x1000 + ((tile & 0xFE) + (r >> 3)) * 16 + (r & 7)
            else:
                a = (0x1000 if ctrl & 0x08 else 0) + tile * 16 + (r & 7)
            m = chr_data[a] | chr_data[a + 8]
            if not m:
                continue
            for col in range(8):
                sx = x0 + col
                if sx >= 255:
                    break
                bit = col if attr & 0x40 else 7 - col
                if (m >> bit) & 1 and self._bg_opaque(sx, sy):
                    return int(sy * self.CYCLES_PER_LINE + (sx + 1) / 3)
        return -1

    def _check_sprite0(self):
        if self.registers[1] & 0x18 != 0x18:
            return False
        if self._s0_cache is None:
            self._s0_cache = self._find_sprite0()
        if self._s0_cache >= 0 and self.frame_cycle >= self._s0_cache:
            self.sprite0_hit = True
        return self.sprite0_hit

    # ------------------------------------------------------------- registers

    def read_register(self, address: int) -> int:
        idx = address & 7

        if idx == 2:
            status = 0x80 if self.vblank_active else 0
            if self.sprite0_hit or self._check_sprite0():
                status |= 0x40
            self.vblank_active = False
            self._nmi_pending = False
            self.address_latch = 0
            return status

        if idx == 4:
            return self.oam[self.oam_addr]

        if idx == 7:
            addr = self.vram_address & 0x3FFF
            result = self.vram_buffered_read
            self.vram_buffered_read = self.read_vram(addr)
            if addr >= 0x3F00:
                result = self.vram_buffered_read
            self.vram_address = (self.vram_address + (32 if self.registers[0] & 0x04 else 1)) & 0x7FFF
            return result

        return self.registers[idx]

    def write_register(self, address: int, value: int):
        idx = address & 7
        old = self.registers[idx]
        self.registers[idx] = value

        if idx == 0:
            # Real hardware raises an NMI only when bit 7 goes from 0 to 1 during vblank.
            # (Firing on every write with bit 7 set makes an NMI handler that writes $2000
            # re-trigger itself again and again.)
            if (value & 0x80) and not (old & 0x80) and self.vblank_active:
                self._nmi_pending = True
            self.t = (self.t & 0xF3FF) | ((value & 3) << 10)
            self._scroll_event(True, False)
        elif idx == 3:
            self.oam_addr = value
        elif idx == 4:
            self.oam[self.oam_addr] = value
            self.oam_addr = (self.oam_addr + 1) & 0xFF
            self._s0_cache = None
        elif idx == 5:
            if self.address_latch == 0:
                self.t = (self.t & 0xFFE0) | (value >> 3)
                self.fine_x = value & 7
                self.address_latch = 1
                self._scroll_event(True, False)
            else:
                self.t = (self.t & 0x8C1F) | ((value & 7) << 12) | ((value & 0xF8) << 2)
                self.address_latch = 0
        elif idx == 6:
            if self.address_latch == 0:
                self.t = (self.t & 0x80FF) | ((value & 0x3F) << 8)
                self.address_latch = 1
            else:
                self.t = (self.t & 0xFF00) | value
                self.vram_address = self.t
                self.address_latch = 0
                self._scroll_event(True, True)
        elif idx == 7:
            self.write_vram(self.vram_address & 0x3FFF, value)
            self.vram_address = (self.vram_address + (32 if self.registers[0] & 0x04 else 1)) & 0x7FFF

    def oam_dma(self, data):
        start = self.oam_addr
        if start == 0:
            self.oam[:] = bytes(data)
        else:
            for i in range(256):
                self.oam[(start + i) & 0xFF] = data[i]
        self._s0_cache = None

    # ------------------------------------------------------------ PPU memory

    def _nt_index(self, addr: int) -> int:
        addr = (addr - 0x2000) & 0x0FFF
        table = addr >> 10
        m = self.mirroring
        if m == 1:          # vertical
            table &= 1
        elif m == 0:        # horizontal
            table >>= 1
        elif m == 2:        # one-screen, lower bank
            table = 0
        else:               # one-screen, upper bank
            table = 1
        return (table << 10) | (addr & 0x3FF)

    @staticmethod
    def _pal_index(addr: int) -> int:
        i = addr & 0x1F
        if (i & 0x13) == 0x10:
            i &= 0x0F
        return i

    def read_vram(self, addr: int) -> int:
        if addr < 0x2000:
            return self.chr_rom[addr]
        if addr < 0x3F00:
            return self.vram[self._nt_index(addr)]
        if addr < 0x4000:
            return self.palette[self._pal_index(addr)]
        return 0

    def write_vram(self, addr: int, value: int):
        if addr < 0x2000:
            if self.chr_writable and self.chr_rom[addr] != value:   # CHR-RAM
                self.chr_rom[addr] = value
                self.chr_version += 1
        elif addr < 0x3F00:
            self.vram[self._nt_index(addr)] = value
        elif addr < 0x4000:
            self.palette[self._pal_index(addr)] = value & 0x3F

    # -------------------------------------------------------------- renderer

    def render_frame(self, fb: bytearray):
        mask = self.registers[1]
        if not (mask & 0x08):
            fb[:] = PALETTE_BYTES[self.palette[0] & 0x3F] * (256 * 240)   # background disabled
            self.bg_mask[:] = self._zero_mask
        else:
            self._render_background(fb)
        if mask & 0x10:
            self.render_sprites(fb)

    def _render_background(self, fb):
        bands = self._bands
        n = len(bands)

        # which of the four nametables are visible this frame
        needed = set()
        for i in range(n):
            start, x, y = bands[i]
            cnt = (bands[i + 1][0] if i + 1 < n else 240) - start
            if cnt <= 0:
                continue
            xs = {(x >> 8) & 1, ((x + 255) >> 8) & 1}
            if cnt >= 240:
                ys = {0, 1}
            else:
                y0 = y % 480
                ys = {y0 // 240, ((y0 + cnt - 1) % 480) // 240}
            for xi in xs:
                for yi in ys:
                    needed.add(xi + 2 * yi)
        self._update_map(needed)

        mv = self._map_mv
        mm = self._map_mask_mv
        sm = self.bg_mask
        for i in range(n):
            start, x, y = bands[i]
            end = bands[i + 1][0] if i + 1 < n else 240
            sx = x & 511
            for line in range(start, end):
                sy = (y + line - start) % 480
                ro = sy * 1536
                mo = sy * 512
                o = line * 768
                po = line * 256
                if sx <= 256:
                    fb[o:o + 768] = mv[ro + sx * 3:ro + sx * 3 + 768]
                    sm[po:po + 256] = mm[mo + sx:mo + sx + 256]
                else:                                   # window wraps around the right edge of the map
                    k = 512 - sx
                    fb[o:o + k * 3] = mv[ro + sx * 3:ro + 1536]
                    fb[o + k * 3:o + 768] = mv[ro:ro + (256 - k) * 3]
                    sm[po:po + k] = mm[mo + sx:mo + 512]
                    sm[po + k:po + 256] = mm[mo:mo + 256 - k]

    def _update_map(self, needed):
        pat_base = 0x1000 if (self.registers[0] & 0x10) else 0x0000
        pal_sig = bytes(self.palette)
        ctx = (pat_base, pal_sig, self.chr_version)
        if ctx != self._map_ctx:                        # palette / pattern / CHR bank changed: redraw all
            self._map_ctx = ctx
            self._tile_cache.clear()
            self._nt_sigs = [None] * 4
            self._map_keys = [[-1] * 960 for _ in range(4)]
            pal = self.palette
            backdrop = PALETTE_BYTES[pal[0] & 0x3F]
            self._map_colors = [[backdrop] + [PALETTE_BYTES[pal[p * 4 + c] & 0x3F] for c in (1, 2, 3)]
                                for p in range(4)]

        colors = self._map_colors
        chr_data = self.chr_rom
        cache = self._tile_cache
        mp = self._map
        mk = self._map_mask

        for nti in needed:
            phys = self._nt_index(0x2000 + nti * 0x400)
            tiles = bytes(self.vram[phys:phys + 960])
            attrs = bytes(self.vram[phys + 960:phys + 1024])
            sig = (tiles, attrs)
            if sig == self._nt_sigs[nti]:
                continue
            self._nt_sigs[nti] = sig
            keys = self._map_keys[nti]
            ox = (nti & 1) * 256
            oy = (nti >> 1) * 240

            for ty in range(30):
                arow = (ty >> 2) * 8
                sh_y = ((ty >> 1) & 1) * 4
                for tx in range(32):
                    i = ty * 32 + tx
                    tile = tiles[i]
                    pal = (attrs[arow + (tx >> 2)] >> (sh_y + ((tx >> 1) & 1) * 2)) & 3
                    key = tile | (pal << 8)
                    if keys[i] == key:
                        continue
                    keys[i] = key

                    ent = cache.get(key)
                    if ent is None:
                        px = colors[pal]
                        base = pat_base + tile * 16
                        rgb = []
                        msk = []
                        for y in range(8):
                            lo = chr_data[base + y]
                            hi = chr_data[base + y + 8]
                            rgb.append(b"".join([px[c] for c in _row(lo, hi)]))
                            msk.append(_MASK_ROWS[lo | hi])
                        ent = cache[key] = (rgb, msk)

                    rgb, msk = ent
                    po = (oy + ty * 8) * 512 + ox + tx * 8
                    o = po * 3
                    for y in range(8):
                        mp[o:o + 24] = rgb[y]
                        mk[po:po + 8] = msk[y]
                        o += 1536
                        po += 512

    def render_sprites(self, fb: bytearray):
        ctrl = self.registers[0]
        tall = bool(ctrl & 0x20)
        height = 16 if tall else 8
        pat_base = 0x1000 if (ctrl & 0x08) else 0x0000
        chr_data = self.chr_rom
        oam = self.oam
        pal_ram = self.palette
        mask = self.bg_mask

        for i in range(63, -1, -1):
            y = oam[i * 4] + 1
            if y >= 240:
                continue
            tile = oam[i * 4 + 1]
            attr = oam[i * 4 + 2]
            x = oam[i * 4 + 3]

            pbase = 16 + (attr & 3) * 4
            flip_h = attr & 0x40
            flip_v = attr & 0x80
            behind = attr & 0x20
            colors = [None] + [PALETTE_BYTES[pal_ram[pbase + c] & 0x3F] for c in (1, 2, 3)]
            ncols = min(8, 256 - x)

            for row in range(height):
                sy = y + row
                if sy >= 240:
                    break
                r = (height - 1 - row) if flip_v else row
                if tall:
                    a = (tile & 1) * 0x1000 + ((tile & 0xFE) + (r >> 3)) * 16 + (r & 7)
                else:
                    a = pat_base + tile * 16 + (r & 7)
                lo = chr_data[a]
                hi = chr_data[a + 8]
                if not (lo | hi):
                    continue
                cs = _row(lo, hi)
                if flip_h:
                    cs = cs[::-1]
                line = sy * 256 + x
                for col in range(ncols):
                    c = cs[col]
                    if c:
                        if behind and mask[line + col]:
                            continue
                        o = (line + col) * 3
                        fb[o:o + 3] = colors[c]