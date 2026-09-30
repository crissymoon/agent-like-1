"""Reads the metadata block out of a GGUF header without loading the weights.

A GGUF file begins with a small, self-describing block: a magic number, a
version, two counts, and then every key-value pair the writer recorded. For a
three gigabyte model that block is a few kilobytes at the front of the file, so
a machine can say what a weight file is without paging in the weights, and a
document can describe a model from the file rather than from somebody's memory
of where it was downloaded.

The reader exists because the project describes models in more than one place
and the descriptions must not disagree. Everything it returns comes from the
file. Nothing is inferred from the file name, which is what lets the caller
compare the two and notice when they disagree.

    >>> header = read_header("models/example.gguf")
    >>> header["metadata"]["general.architecture"]
    'llama'

Only the header is read. The tokenizer vocabulary is the one large value in the
block, an array of a quarter of a million strings, so a string array past the
retention limit is counted and skipped: the count is the useful fact and the
strings are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"GGUF"

#: The value types a metadata entry can carry. The numbers are the format's.
TYPE_UINT8 = 0
TYPE_INT8 = 1
TYPE_UINT16 = 2
TYPE_INT16 = 3
TYPE_UINT32 = 4
TYPE_INT32 = 5
TYPE_FLOAT32 = 6
TYPE_BOOL = 7
TYPE_STRING = 8
TYPE_ARRAY = 9
TYPE_UINT64 = 10
TYPE_INT64 = 11
TYPE_FLOAT64 = 12

_SCALAR_STRUCT = {
    TYPE_UINT8: ("B", 1),
    TYPE_INT8: ("b", 1),
    TYPE_UINT16: ("H", 2),
    TYPE_INT16: ("h", 2),
    TYPE_UINT32: ("I", 4),
    TYPE_INT32: ("i", 4),
    TYPE_FLOAT32: ("f", 4),
    TYPE_BOOL: ("?", 1),
    TYPE_UINT64: ("Q", 8),
    TYPE_INT64: ("q", 8),
    TYPE_FLOAT64: ("d", 8),
}

TYPE_NAMES = {
    TYPE_UINT8: "uint8",
    TYPE_INT8: "int8",
    TYPE_UINT16: "uint16",
    TYPE_INT16: "int16",
    TYPE_UINT32: "uint32",
    TYPE_INT32: "int32",
    TYPE_FLOAT32: "float32",
    TYPE_BOOL: "bool",
    TYPE_STRING: "string",
    TYPE_ARRAY: "array",
    TYPE_UINT64: "uint64",
    TYPE_INT64: "int64",
    TYPE_FLOAT64: "float64",
}

#: An array longer than this is counted and skipped. The only values that reach
#: it are the tokenizer tables, where the length is the interesting number.
MAX_STRING_ARRAY_RETAINED = 32

#: `general.file_type` to the name of the quantisation it stands for. This is the
#: engine's own enumeration, which is why it lives beside the format rather than
#: in a document: the number in the file and the name in a table are the same
#: fact, and a value this table does not know is reported as its number instead
#: of being guessed at. A caller can hold the number against the quantisation in
#: a file's name, which is exactly the disagreement worth catching.
FILE_TYPE_NAMES: dict[int, str] = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "IQ3_XXS",
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ3_M",
    28: "IQ2_S",
    29: "IQ2_M",
    30: "IQ4_XS",
    31: "IQ1_M",
    32: "BF16",
    33: "Q4_0_4_4",
    34: "Q4_0_4_8",
    35: "Q4_0_8_8",
    36: "TQ1_0",
    37: "TQ2_0",
    38: "MXFP4_MOE",
}

#: The set of quantisation words as they appear spelled out in a file name,
#: longest first so that `Q4_K_M` is not read as `Q4_K`. The builder holds a
#: name against the file's own number; it is a cross-check, not a source.
QUANT_WORDS: tuple[str, ...] = (
    "Q4_0_4_4",
    "Q4_0_4_8",
    "Q4_0_8_8",
    "IQ2_XXS",
    "IQ2_XS",
    "IQ3_XXS",
    "IQ3_XS",
    "IQ3_S",
    "IQ3_M",
    "IQ2_S",
    "IQ2_M",
    "IQ1_S",
    "IQ1_M",
    "IQ4_NL",
    "IQ4_XS",
    "MXFP4_MOE",
    "Q3_K_S",
    "Q3_K_M",
    "Q3_K_L",
    "Q4_K_S",
    "Q4_K_M",
    "Q5_K_S",
    "Q5_K_M",
    "Q6_K",
    "Q2_K",
    "Q4_0",
    "Q4_1",
    "Q5_0",
    "Q5_1",
    "Q8_0",
    "TQ1_0",
    "TQ2_0",
    "BF16",
    "F32",
    "F16",
)


def file_type_name(value: int | None) -> str:
    """The quantisation name for an engine file type, or its number."""
    if value is None:
        return ""
    return FILE_TYPE_NAMES.get(int(value), f"unlisted-{int(value)}")


def quant_word(*candidates: str) -> str:
    """The quantisation word written into a file name, or the empty string.

    The search is case-insensitive and prefers the longest word so that a name
    holding `q4_k_m` is not read as `q4`.
    """
    for text in candidates:
        upper = text.upper()
        for word in QUANT_WORDS:
            if word in upper:
                return word
    return ""

#: Hard stop for the metadata walk, so a corrupt count cannot make the reader
#: read a whole weight file looking for pairs that are not there.
MAX_METADATA_BYTES = 64 * 1024 * 1024


class GgufError(RuntimeError):
    """The file is not a GGUF file, or its header cannot be read."""


@dataclass
class _Reader:
    """A sequential reader over the front of a file."""

    handle: object
    path: str
    consumed: int = 0

    def read(self, count: int) -> bytes:
        if self.consumed + count > MAX_METADATA_BYTES:
            raise GgufError(f"{self.path}: metadata block exceeds the read limit")
        data = self.handle.read(count)  # type: ignore[attr-defined]
        if len(data) != count:
            raise GgufError(f"{self.path}: header ends after {self.consumed} bytes")
        self.consumed += count
        return data

    def skip(self, count: int) -> None:
        if count <= 0:
            return
        if self.consumed + count > MAX_METADATA_BYTES:
            raise GgufError(f"{self.path}: metadata block exceeds the read limit")
        self.handle.seek(count, 1)  # type: ignore[attr-defined]
        self.consumed += count

    def uint32(self) -> int:
        return int.from_bytes(self.read(4), "little")

    def uint64(self) -> int:
        return int.from_bytes(self.read(8), "little")

    def string(self) -> str:
        length = self.uint64()
        if length > MAX_METADATA_BYTES:
            raise GgufError(f"{self.path}: string length {length} is implausible")
        return self.read(int(length)).decode("utf-8", errors="replace")

    def scalar(self, value_type: int):
        struct_format, size = _SCALAR_STRUCT[value_type]
        import struct

        return struct.unpack("<" + struct_format, self.read(size))[0]


def _read_value(reader: _Reader, value_type: int):
    if value_type == TYPE_STRING:
        return reader.string()
    if value_type == TYPE_ARRAY:
        element_type = reader.uint32()
        count = reader.uint64()
        return _read_array(reader, element_type, int(count))
    if value_type in _SCALAR_STRUCT:
        return reader.scalar(value_type)
    raise GgufError(f"{reader.path}: unknown value type {value_type}")


def _read_array(reader: _Reader, element_type: int, count: int):
    """Reads an array, keeping it only when keeping it is cheap.

    A fixed-width array is skipped by seeking, so a long numeric array costs one
    seek. A string array has to be walked to find the end of it, so a long one is
    walked and discarded, and reported as its length.
    """
    if count == 0:
        return []

    if element_type in _SCALAR_STRUCT:
        _, size = _SCALAR_STRUCT[element_type]
        if count > MAX_STRING_ARRAY_RETAINED:
            reader.skip(count * size)
            return {"count": count, "element_type": TYPE_NAMES[element_type], "retained": False}
        return [reader.scalar(element_type) for _ in range(count)]

    if element_type == TYPE_STRING:
        retained: list[str] = []
        keep = count <= MAX_STRING_ARRAY_RETAINED
        for index in range(count):
            value = reader.string()
            if keep:
                retained.append(value)
            elif index == 0:
                # Nothing is kept, but the reader still has to reach the end.
                continue
        if keep:
            return retained
        return {"count": count, "element_type": "string", "retained": False}

    if element_type == TYPE_ARRAY:
        return [_read_value(reader, TYPE_ARRAY) for _ in range(count)]

    raise GgufError(f"{reader.path}: unknown array element type {element_type}")


@dataclass
class Header:
    """What the front of a GGUF file says."""

    path: str
    version: int
    tensor_count: int
    metadata_count: int
    metadata: dict = field(default_factory=dict)

    def get(self, key: str, default=None):
        return self.metadata.get(key, default)

    def architecture(self) -> str:
        return str(self.metadata.get("general.architecture", ""))

    def arch_value(self, suffix: str, default=None):
        """A key under the file's own architecture, which is how they are named."""
        architecture = self.architecture()
        if architecture == "":
            return default
        return self.metadata.get(f"{architecture}.{suffix}", default)

    def context_length(self) -> int | None:
        value = self.arch_value("context_length")
        return int(value) if isinstance(value, int) else None

    def embedding_length(self) -> int | None:
        value = self.arch_value("embedding_length")
        return int(value) if isinstance(value, int) else None

    def block_count(self) -> int | None:
        value = self.arch_value("block_count")
        return int(value) if isinstance(value, int) else None

    def parameter_count(self) -> int | None:
        value = self.metadata.get("general.parameter_count")
        return int(value) if isinstance(value, (int, float)) else None

    def size_label(self) -> str:
        return str(self.metadata.get("general.size_label", ""))

    def name(self) -> str:
        return str(self.metadata.get("general.name", ""))

    def file_type(self) -> int | None:
        value = self.metadata.get("general.file_type")
        return int(value) if isinstance(value, int) else None

    def quantisation(self) -> str:
        """The quantisation the file says it is, by name where it has one."""
        return file_type_name(self.file_type())

    def summary(self) -> dict:
        """The fields the documents use, all of them read from the file."""
        return {
            "architecture": self.architecture(),
            "name": self.name(),
            "size_label": self.size_label(),
            "parameter_count": self.parameter_count(),
            "context_length": self.context_length(),
            "embedding_length": self.embedding_length(),
            "block_count": self.block_count(),
            "file_type": self.file_type(),
            "quantisation": self.quantisation(),
            "metadata_entries": len(self.metadata),
            "tensor_count": self.tensor_count,
            "gguf_version": self.version,
        }


def read_header(path: str | Path) -> Header:
    """Open a GGUF file, read its metadata block, and close it."""
    source = Path(path)
    if not source.is_file():
        raise GgufError(f"{source}: no such file")

    try:
        with source.open("rb") as handle:
            reader = _Reader(handle=handle, path=str(source))
            if reader.read(4) != MAGIC:
                raise GgufError(f"{source}: not a GGUF file")
            version = reader.uint32()
            if version < 2:
                raise GgufError(f"{source}: GGUF version {version} is older than this reader")
            tensor_count = reader.uint64()
            metadata_count = reader.uint64()

            metadata: dict = {}
            for _ in range(int(metadata_count)):
                key = reader.string()
                value_type = reader.uint32()
                metadata[key] = _read_value(reader, value_type)
    except OSError as error:
        raise GgufError(f"{source}: {error}") from error

    return Header(
        path=str(source),
        version=version,
        tensor_count=int(tensor_count),
        metadata_count=int(metadata_count),
        metadata=metadata,
    )


def read_headers(directory: str | Path, pattern: str = "*.gguf") -> list[Header]:
    """Every readable header in a directory, a bad file reported and skipped."""
    folder = Path(directory)
    if not folder.is_dir():
        return []

    headers: list[Header] = []
    for candidate in sorted(folder.glob(pattern)):
        if not candidate.is_file():
            continue
        try:
            headers.append(read_header(candidate))
        except GgufError as error:
            print(f"gguf: skipped {error}")
    return headers


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="gguf",
        description="Print the metadata header of each GGUF file in a directory.",
    )
    parser.add_argument("directory", nargs="?", default="models")
    parser.add_argument("--json", action="store_true", help="emit the full metadata block")
    args = parser.parse_args(argv)

    headers = read_headers(args.directory)
    if not headers:
        print(f"gguf: no readable GGUF file under {args.directory}")
        return 1

    for header in headers:
        print(f"{Path(header.path).name}")
        if args.json:
            print(json.dumps(header.metadata, indent=2, default=str))
            continue
        for key, value in header.summary().items():
            print(f"  {key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
