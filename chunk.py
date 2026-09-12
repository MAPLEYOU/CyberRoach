# -*- coding: utf-8 -*-
"""Python 3.13 移除了 stdlib chunk 模块, 这里补一个兼容垫片(仅读取用)."""
import struct


class Chunk:
    def __init__(self, file, align=True, bigendian=True):
        self.closed = False
        self.chunkname = file.read(4)
        try:
            self.chunkname = self.chunkname.decode("ascii")
        except UnicodeDecodeError:
            pass
        try:
            self.size = struct.unpack(bigendian and ">I" or "<I",
                                      file.read(4))[0]
        except struct.error:
            raise EOFError
        self.read_size = self.size
        if self.chunkname == b"RIFF" or self.chunkname == "RIFF":
            self.chunkname = file.read(4)
        self.size_read = 0
        self.file = file
        self.align = align
        self.bigendian = bigendian

    def getname(self):
        if isinstance(self.chunkname, bytes):
            return self.chunkname.decode("ascii", "replace")
        return self.chunkname

    def getsize(self):
        return self.size

    def read(self, size=-1):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        if self.size_read >= self.size:
            return b""
        if size < 0:
            size = self.size - self.size_read
        if size > self.size - self.size_read:
            size = self.size - self.size_read
        data = self.file.read(size)
        self.size_read += len(data)
        return data

    def close(self):
        if not self.closed:
            self.closed = True
            if self.size_read < self.size:
                n = self.size - self.size_read
                self.file.seek(n, 1)
                self.size_read = self.size
            if self.align and (self.size & 1):
                self.file.seek(1, 1)

    def isatty(self):
        return False

    def seek(self, pos, whence=0):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        if not isinstance(pos, int):
            raise TypeError("argument must be integer")
        if whence == 1:
            pos += self.size_read
        elif whence == 2:
            pos += self.size
        if pos < 0 or pos > self.size:
            raise RuntimeError("Bad file descriptor")
        self.file.seek(self.size - self.size_read + pos, 1)
        self.size_read = pos

    def tell(self):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return self.size_read

    def skip(self):
        if self.closed:
            raise ValueError("I/O operation on closed file")
        if self.size_read < self.size:
            n = self.size - self.size_read
            self.file.seek(n, 1)
            self.size_read = self.size
