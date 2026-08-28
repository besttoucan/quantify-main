"""A small QR code encoder, used so nobody has to type a security key by hand.

Byte mode, error correction level M, versions 1 through 12. That is enough for
an otpauth enrolment link and keeps the file short enough to read and verify.
The output is a square grid of booleans plus an SVG helper.
"""

from __future__ import annotations

# (error correction codewords per block, blocks in group 1, data codewords in
# group 1, blocks in group 2, data codewords in group 2) for level M.
BLOCK_TABLE: dict[int, tuple[int, int, int, int, int]] = {
    1: (10, 1, 16, 0, 0),
    2: (16, 1, 28, 0, 0),
    3: (26, 1, 44, 0, 0),
    4: (18, 2, 32, 0, 0),
    5: (24, 2, 43, 0, 0),
    6: (16, 4, 27, 0, 0),
    7: (18, 4, 31, 0, 0),
    8: (22, 2, 38, 2, 39),
    9: (22, 3, 36, 2, 37),
    10: (26, 4, 43, 1, 44),
    11: (30, 1, 50, 4, 51),
    12: (22, 6, 36, 2, 37),
}

ALIGNMENT_CENTERS: dict[int, list[int]] = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
    11: [6, 30, 54], 12: [6, 32, 58],
}

ECL_M_BITS = 0  # Level M is encoded as 00 in the format information.

_EXP: list[int] = [0] * 512
_LOG: list[int] = [0] * 256


def _build_tables() -> None:
    value = 1
    for index in range(255):
        _EXP[index] = value
        _LOG[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        _EXP[index] = _EXP[index - 255]


_build_tables()


def _gf_multiply(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return _EXP[_LOG[left] + _LOG[right]]


def _generator_polynomial(degree: int) -> list[int]:
    """The product of (x + a^0)(x + a^1)...(x + a^degree-1), highest power first."""
    poly = [1]
    for index in range(degree):
        shifted = [0] * (len(poly) + 1)
        for position, coefficient in enumerate(poly):
            shifted[position] ^= coefficient
            shifted[position + 1] ^= _gf_multiply(coefficient, _EXP[index])
        poly = shifted
    return poly


def reed_solomon(data: list[int], ec_count: int) -> list[int]:
    generator = _generator_polynomial(ec_count)
    remainder = [0] * ec_count
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for index in range(ec_count):
            remainder[index] ^= _gf_multiply(generator[index + 1], factor)
    return remainder


def _raw_data_modules(version: int) -> int:
    total = (16 * version + 128) * version + 64
    if version >= 2:
        alignments = version // 7 + 2
        total -= (25 * alignments - 10) * alignments - 55
        if version >= 7:
            total -= 36
    return total


def _data_capacity_bytes(version: int) -> int:
    ec_per_block, blocks1, data1, blocks2, data2 = BLOCK_TABLE[version]
    return blocks1 * data1 + blocks2 * data2


def _choose_version(payload: bytes) -> int:
    for version in sorted(BLOCK_TABLE):
        count_bits = 8 if version <= 9 else 16
        needed = 4 + count_bits + len(payload) * 8
        if needed <= _data_capacity_bytes(version) * 8:
            return version
    raise ValueError("This value is too long for the supported QR versions")


def _encode_payload(payload: bytes, version: int) -> list[int]:
    capacity_bits = _data_capacity_bytes(version) * 8
    bits: list[int] = []

    def push(value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    push(0b0100, 4)                       # byte mode
    push(len(payload), 8 if version <= 9 else 16)
    for byte in payload:
        push(byte, 8)
    push(0, min(4, capacity_bits - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    codewords = [int("".join(str(bit) for bit in bits[index:index + 8]), 2) for index in range(0, len(bits), 8)]
    padding = (0xEC, 0x11)
    position = 0
    while len(codewords) < capacity_bits // 8:
        codewords.append(padding[position % 2])
        position += 1
    return codewords


def _interleave(codewords: list[int], version: int) -> list[int]:
    ec_per_block, blocks1, data1, blocks2, data2 = BLOCK_TABLE[version]
    blocks: list[list[int]] = []
    cursor = 0
    for _ in range(blocks1):
        blocks.append(codewords[cursor:cursor + data1])
        cursor += data1
    for _ in range(blocks2):
        blocks.append(codewords[cursor:cursor + data2])
        cursor += data2
    ec_blocks = [reed_solomon(block, ec_per_block) for block in blocks]

    result: list[int] = []
    longest = max(len(block) for block in blocks)
    for index in range(longest):
        for block in blocks:
            if index < len(block):
                result.append(block[index])
    for index in range(ec_per_block):
        for block in ec_blocks:
            result.append(block[index])
    return result


def _format_bits(mask: int) -> int:
    data = (ECL_M_BITS << 3) | mask
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return ((data << 10) | remainder) ^ 0x5412


def _version_bits(version: int) -> int:
    remainder = version
    for _ in range(12):
        remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
    return (version << 12) | remainder


class _Matrix:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.reserved = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self.reserved[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for index in range(size):
            self.set_function(6, index, index % 2 == 0)
            self.set_function(index, 6, index % 2 == 0)
        for center_x, center_y in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    distance = max(abs(dx), abs(dy))
                    x, y = center_x + dx, center_y + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_function(x, y, distance not in (2, 4))
        centers = ALIGNMENT_CENTERS[self.version]
        for row_index, center_y in enumerate(centers):
            for col_index, center_x in enumerate(centers):
                skip_corner = (
                    (row_index == 0 and col_index == 0)
                    or (row_index == 0 and col_index == len(centers) - 1)
                    or (row_index == len(centers) - 1 and col_index == 0)
                )
                if skip_corner:
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_function(center_x + dx, center_y + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format_bits(0)
        if self.version >= 7:
            bits = _version_bits(self.version)
            for index in range(18):
                dark = (bits >> index) & 1 == 1
                a = size - 11 + index % 3
                b = index // 3
                self.set_function(a, b, dark)
                self.set_function(b, a, dark)

    def draw_format_bits(self, mask: int) -> None:
        size = self.size
        bits = _format_bits(mask)

        def bit(position: int) -> bool:
            return (bits >> position) & 1 == 1

        for index in range(6):
            self.set_function(8, index, bit(index))
        self.set_function(8, 7, bit(6))
        self.set_function(8, 8, bit(7))
        self.set_function(7, 8, bit(8))
        for index in range(9, 15):
            self.set_function(14 - index, 8, bit(index))
        for index in range(8):
            self.set_function(size - 1 - index, 8, bit(index))
        for index in range(8, 15):
            self.set_function(8, size - 15 + index, bit(index))
        self.set_function(8, size - 8, True)

    def place_data(self, codewords: list[int]) -> None:
        size = self.size
        position = 0
        total = len(codewords) * 8
        column = size - 1
        while column > 0:
            if column == 6:
                column = 5
            for vertical in range(size):
                for offset in range(2):
                    x = column - offset
                    upward = ((column + 1) & 2) == 0
                    y = (size - 1 - vertical) if upward else vertical
                    if not self.reserved[y][x] and position < total:
                        byte = codewords[position >> 3]
                        self.modules[y][x] = (byte >> (7 - (position & 7))) & 1 == 1
                        position += 1
            column -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self.reserved[y][x]:
                    continue
                if mask == 0:
                    invert = (x + y) % 2 == 0
                elif mask == 1:
                    invert = y % 2 == 0
                elif mask == 2:
                    invert = x % 3 == 0
                elif mask == 3:
                    invert = (x + y) % 3 == 0
                elif mask == 4:
                    invert = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    invert = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    invert = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    invert = ((x + y) % 2 + x * y % 3) % 2 == 0
                if invert:
                    self.modules[y][x] = not self.modules[y][x]

    def penalty(self) -> int:
        size = self.size
        score = 0
        for y in range(size):
            run_colour = self.modules[y][0]
            run = 1
            for x in range(1, size):
                if self.modules[y][x] == run_colour:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run_colour = self.modules[y][x]
                    run = 1
            if run >= 5:
                score += 3 + (run - 5)
        for x in range(size):
            run_colour = self.modules[0][x]
            run = 1
            for y in range(1, size):
                if self.modules[y][x] == run_colour:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run_colour = self.modules[y][x]
                    run = 1
            if run >= 5:
                score += 3 + (run - 5)
        for y in range(size - 1):
            for x in range(size - 1):
                block = self.modules[y][x]
                if block == self.modules[y][x + 1] == self.modules[y + 1][x] == self.modules[y + 1][x + 1]:
                    score += 3
        pattern = [True, False, True, True, True, False, True]
        for y in range(size):
            for x in range(size - 6):
                if self.modules[y][x:x + 7] == pattern:
                    before = all(not self.modules[y][index] for index in range(max(0, x - 4), x))
                    after = all(not self.modules[y][index] for index in range(x + 7, min(size, x + 11)))
                    if (x - 4 < 0 or before) and (x + 11 > size or after):
                        score += 40
        for x in range(size):
            column = [self.modules[y][x] for y in range(size)]
            for y in range(size - 6):
                if column[y:y + 7] == pattern:
                    before = all(not value for value in column[max(0, y - 4):y])
                    after = all(not value for value in column[y + 7:min(size, y + 11)])
                    if (y - 4 < 0 or before) and (y + 11 > size or after):
                        score += 40
        dark = sum(1 for row in self.modules for value in row if value)
        total = size * size
        percent = dark * 100 / total
        score += int(abs(percent - 50) / 5) * 10
        return score


def encode(text: str) -> list[list[bool]]:
    """Return the QR grid for `text` as rows of booleans (True is a dark module)."""
    payload = text.encode("utf-8")
    version = _choose_version(payload)
    codewords = _interleave(_encode_payload(payload, version), version)

    best_matrix: _Matrix | None = None
    best_score = -1
    for mask in range(8):
        matrix = _Matrix(version)
        matrix.draw_function_patterns()
        matrix.place_data(codewords)
        matrix.apply_mask(mask)
        matrix.draw_format_bits(mask)
        score = matrix.penalty()
        if best_matrix is None or score < best_score:
            best_matrix, best_score = matrix, score
    assert best_matrix is not None
    return best_matrix.modules


def svg(text: str, quiet_zone: int = 3) -> str:
    """Render the QR grid as a self-contained SVG string."""
    grid = encode(text)
    size = len(grid) + quiet_zone * 2
    parts: list[str] = []
    for y, row in enumerate(grid):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            run = 1
            while x + run < len(row) and row[x + run]:
                run += 1
            parts.append(f"M{x + quiet_zone} {y + quiet_zone}h{run}v1h-{run}z")
            x += run
    path = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" role="img" aria-label="Two-factor setup code">'
        f'<rect width="{size}" height="{size}" fill="#ffffff"/>'
        f'<path d="{path}" fill="#101215"/></svg>'
    )
