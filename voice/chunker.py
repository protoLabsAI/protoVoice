import re


class SentenceChunker:
    BOUNDARY = re.compile(r'(?<=[.!?;:])\s+|(?<=[.!?])\s*$')

    def __init__(self, min_first=10, min_rest=30, max_chars=200):
        self.buffer = ""
        self.chunk_count = 0
        self.min_first = min_first
        self.min_rest = min_rest
        self.max_chars = max_chars

    @property
    def min_chars(self):
        return self.min_first if self.chunk_count == 0 else self.min_rest

    def feed(self, token: str):
        self.buffer += token
        if len(self.buffer) >= self.max_chars:
            text = self.buffer.strip()
            if text:
                self.chunk_count += 1
                yield text
            self.buffer = ""
            return
        pattern = re.compile(r'(?<=[,.!?;:])\s+') if self.chunk_count == 0 else self.BOUNDARY
        matches = list(pattern.finditer(self.buffer))
        if matches:
            last = matches[-1]
            candidate = self.buffer[:last.end()].strip()
            if len(candidate) >= self.min_chars:
                self.chunk_count += 1
                yield candidate
                self.buffer = self.buffer[last.end():]

    def flush(self):
        if self.buffer.strip():
            self.chunk_count += 1
            yield self.buffer.strip()
            self.buffer = ""
