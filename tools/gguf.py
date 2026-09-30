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

#: The per-tensor type identifier, which is a *different* enumeration from
#: `general.file_type` above: that one labels the file as a whole, this one
#: labels one tensor, and the same number means different things in each. The
#: names come from the `gguf` package when it is installed, because it owns the
#: enumeration; the table below is the fallback for a checkout without it and
#: covers the types an ordinary quantised model is written with. An identifier
#: neither source knows is reported as its number rather than guessed at.
_FALLBACK_TENSOR_TYPE_NAMES: dict[int, str] = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    9: "Q8_1",
    10: "Q2_K",
    11: "Q3_K",
    12: "Q4_K",
    13: "Q5_K",
    14: "Q6_K",
    15: "Q8_K",
    16: "IQ2_XXS",
    17: "IQ2_XS",
    18: "IQ3_XXS",
    19: "IQ1_S",
    20: "IQ4_NL",
    21: "IQ3_S",
    22: "IQ2_S",
    23: "IQ4_XS",
    24: "I8",
    25: "I16",
    26: "I32",
    27: "I64",
    28: "F64",
    29: "IQ1_M",
    30: "BF16",
    34: "TQ1_0",
    35: "TQ2_0",
    39: "MXFP4",
    40: "NVFP4",
    41: "Q1_0",
}

#: A quantised type's `(block_size, type_size)`: how many numbers a block holds
#: and how many bytes that block occupies. The numbers are the format's, and the
#: `gguf` package is preferred as the source; this is the fallback so that the
#: arithmetic still runs on a machine that has not installed it. A type with no
#: entry here has no size, and a caller reports that rather than guessing.
_FALLBACK_TENSOR_TYPE_SIZES: dict[int, tuple[int, int]] = {
    0: (1, 4),
    1: (1, 2),
    2: (32, 18),
    3: (32, 20),
    6: (32, 22),
    7: (32, 24),
    8: (32, 34),
    9: (32, 40),
    10: (256, 84),
    11: (256, 110),
    12: (256, 144),
    13: (256, 176),
    14: (256, 210),
    15: (256, 292),
    16: (256, 66),
    17: (256, 74),
    18: (256, 98),
    19: (256, 50),
    20: (32, 18),
    21: (256, 110),
    22: (256, 82),
    23: (256, 136),
    24: (1, 1),
    25: (1, 2),
    26: (1, 4),
    27: (1, 8),
    28: (1, 8),
    29: (256, 56),
    30: (1, 2),
    34: (256, 54),
    35: (256, 66),
    39: (32, 17),
    40: (64, 36),
    41: (128, 18),
}

#: Type identifiers whose blocks are single elements, so a tensor stored as one
#: of these holds exactly what it says and loading it expands nothing.
_PLAIN_TYPE_IDS = frozenset({0, 1, 24, 25, 26, 27, 28, 30})

_tensor_tables: tuple[dict[int, str], dict[int, tuple[int, int]]] | None = None


def _tensor_tables_cached() -> tuple[dict[int, str], dict[int, tuple[int, int]]]:
    """The per-tensor type names and block sizes, from `gguf` where present."""
    global _tensor_tables
    if _tensor_tables is not None:
        return _tensor_tables

    names: dict[int, str] = dict(_FALLBACK_TENSOR_TYPE_NAMES)
    sizes: dict[int, tuple[int, int]] = dict(_FALLBACK_TENSOR_TYPE_SIZES)
    try:
        import gguf

        for member in gguf.GGMLQuantizationType:
            names[int(member)] = member.name
        for key, value in gguf.GGML_QUANT_SIZES.items():
            sizes[int(key)] = (int(value[0]), int(value[1]))
    except Exception:
        # Without the package the fallback tables above carry the answer, and a
        # type absent from both is reported as unknown rather than assumed.
        pass

    _tensor_tables = (names, sizes)
    return _tensor_tables


def tensor_type_name(value: int) -> str:
    """The name of a per-tensor GGML type, or its number when unlisted."""
    names, _ = _tensor_tables_cached()
    return names.get(int(value), f"type-{int(value)}")


def tensor_type_size(value: int) -> tuple[int, int] | None:
    """A tensor type's `(block_size, type_size)` in bytes, or None if unknown."""
    _, sizes = _tensor_tables_cached()
    return sizes.get(int(value))


def tensor_type_is_quantised(value: int) -> bool:
    """Whether a tensor type stores blocks rather than plain elements."""
    return int(value) not in _PLAIN_TYPE_IDS


#: Hard stop for the header walk, so a corrupt count cannot make the reader
#: read a whole weight file looking for pairs that are not there.
MAX_METADATA_BYTES = 64 * 1024 * 1024

#: Hard stop for the tensor table, which is a row of a few hundred bytes per
#: tensor. A real model reaches a few hundred kilobytes; this is the "something
#: is wrong with the count" guard.
MAX_TENSOR_BYTES = 256 * 1024 * 1024


class GgufError(RuntimeError):
    """The file is not a GGUF file, or its header cannot be read."""


@dataclass
class _Reader:
    """A sequential reader over the front of a file."""

    handle: object
    path: str
    consumed: int = 0
    limit: int = MAX_METADATA_BYTES

    def read(self, count: int) -> bytes:
        if self.consumed + count > self.limit:
            raise GgufError(f"{self.path}: header exceeds the read limit")
        data = self.handle.read(count)  # type: ignore[attr-defined]
        if len(data) != count:
            raise GgufError(f"{self.path}: header ends after {self.consumed} bytes")
        self.consumed += count
        return data

    def skip(self, count: int) -> None:
        if count <= 0:
            return
        if self.consumed + count > self.limit:
            raise GgufError(f"{self.path}: header exceeds the read limit")
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


@dataclass(frozen=True)
class TensorInfo:
    """One row of the tensor table: what a weight is called, shaped, and stored as.

    The dimensions are in the file's own order, which is the reverse of a torch
    shape: a GGUF `(in, out)` pair is a torch weight of shape `(out, in)`.
    """

    name: str
    dims: tuple[int, ...]
    type_id: int
    offset: int

    @property
    def type_name(self) -> str:
        return tensor_type_name(self.type_id)

    @property
    def quantised(self) -> bool:
        return tensor_type_is_quantised(self.type_id)

    def elements(self) -> int:
        """How many numbers the tensor holds, before any packing."""
        total = 1
        for size in self.dims:
            total *= size
        return total

    def packed_bytes(self) -> int | None:
        """How many bytes the tensor occupies, or None for an unknown type."""
        sizes = tensor_type_size(self.type_id)
        if sizes is None:
            return None
        block, width = sizes
        elements = self.elements()
        return -(-elements // block) * width


@dataclass
class TensorIndex:
    """The tensor table of a GGUF file, and a little arithmetic over it.

    This is what lets a caller hold a weight file against what a profile says
    the file must contain, before gigabytes of anything are downloaded.
    """

    path: str
    version: int
    tensors: list[TensorInfo]
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._by_name = {tensor.name: tensor for tensor in self.tensors}

    def names(self) -> frozenset[str]:
        return frozenset(self._by_name)

    def find(self, name: str) -> TensorInfo | None:
        return self._by_name.get(name)

    def dims(self, name: str) -> tuple[int, ...] | None:
        tensor = self._by_name.get(name)
        return tensor.dims if tensor is not None else None

    def block_indices(self, prefix: str) -> tuple[int, ...]:
        """The distinct numeric indices under a name prefix, sorted.

        A prefix of `double_blocks` over `double_blocks.7.attn.qkv.weight`
        yields 7, so the count of returned numbers is the block count, and a
        gap in them is visible rather than hidden by a count.
        """
        stem = prefix.rstrip(".") + "."
        found: set[int] = set()
        for name in self._by_name:
            if not name.startswith(stem):
                continue
            remainder = name[len(stem) :]
            head = remainder.split(".", 1)[0]
            if head.isdigit():
                found.add(int(head))
        return tuple(sorted(found))

    def type_histogram(self) -> dict[str, int]:
        """How many tensors are stored as each type, most common first."""
        counts: dict[str, int] = {}
        for tensor in self.tensors:
            counts[tensor.type_name] = counts.get(tensor.type_name, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def elements(self) -> int:
        return sum(tensor.elements() for tensor in self.tensors)

    def packed_bytes(self) -> int | None:
        """Bytes the weights occupy as stored, or None if a type is unknown."""
        total = 0
        for tensor in self.tensors:
            size = tensor.packed_bytes()
            if size is None:
                return None
            total += size
        return total

    def exact_bytes(self) -> int:
        """Bytes the weights would occupy if every one were held in BF16."""
        return self.elements() * 2

    def quantised_count(self) -> int:
        return sum(1 for tensor in self.tensors if tensor.quantised)

    def unknown_types(self) -> tuple[int, ...]:
        """Type identifiers this machine has no size for, so a check can refuse."""
        unknown = {
            tensor.type_id
            for tensor in self.tensors
            if tensor_type_size(tensor.type_id) is None
        }
        return tuple(sorted(unknown))


@dataclass
class _Content:
    """One pass over the front of a file: the metadata, and optionally the table."""

    path: str
    version: int
    tensor_count: int
    metadata_count: int
    metadata: dict
    tensors: list[TensorInfo]


def _walk(path: str | Path, with_tensors: bool) -> _Content:
    """Read the opening block of a GGUF file, stopping before the weights."""
    source = Path(path)
    if not source.is_file():
        raise GgufError(f"{source}: no such file")

    limit = MAX_TENSOR_BYTES if with_tensors else MAX_METADATA_BYTES
    try:
        with source.open("rb") as handle:
            reader = _Reader(handle=handle, path=str(source), limit=limit)
            if reader.read(4) != MAGIC:
                raise GgufError(f"{source}: not a GGUF file")
            version = reader.uint32()
            if version < 2:
                raise GgufError(f"{source}: GGUF version {version} is older than this reader")
            tensor_count = int(reader.uint64())
            metadata_count = int(reader.uint64())

            metadata: dict = {}
            for _ in range(metadata_count):
                key = reader.string()
                value_type = reader.uint32()
                metadata[key] = _read_value(reader, value_type)

            tensors: list[TensorInfo] = []
            if with_tensors:
                for _ in range(tensor_count):
                    name = reader.string()
                    rank = reader.uint32()
                    dims = tuple(int(reader.uint64()) for _ in range(rank))
                    type_id = reader.uint32()
                    offset = int(reader.uint64())
                    tensors.append(TensorInfo(name=name, dims=dims, type_id=type_id, offset=offset))
    except OSError as error:
        raise GgufError(f"{source}: {error}") from error

    return _Content(
        path=str(source),
        version=version,
        tensor_count=tensor_count,
        metadata_count=metadata_count,
        metadata=metadata,
        tensors=tensors,
    )


def read_header(path: str | Path) -> Header:
    """Open a GGUF file, read its metadata block, and close it."""
    content = _walk(path, with_tensors=False)
    return Header(
        path=content.path,
        version=content.version,
        tensor_count=content.tensor_count,
        metadata_count=content.metadata_count,
        metadata=content.metadata,
    )


def read_index(path: str | Path) -> TensorIndex:
    """Open a GGUF file, read its metadata and tensor table, and close it."""
    content = _walk(path, with_tensors=True)
    return TensorIndex(
        path=content.path,
        version=content.version,
        tensors=content.tensors,
        metadata=content.metadata,
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
    parser.add_argument(
        "--tensors",
        nargs="*",
        metavar="NAME",
        help="print the tensor table, or just the named tensors",
    )
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
        if args.tensors is not None:
            _print_tensors(header.path, args.tensors)

    return 0


def _print_tensors(path: str, wanted: list[str]) -> None:
    index = read_index(path)
    if wanted:
        for name in wanted:
            tensor = index.find(name)
            if tensor is None:
                print(f"  {name}: absent")
                continue
            print(f"  {name}: {tensor.dims} {tensor.type_name} {tensor.packed_bytes()} bytes")
        return

    packed = index.packed_bytes()
    print(f"  tensors: {len(index.tensors)} ({index.quantised_count()} quantised)")
    print(f"  type histogram: {index.type_histogram()}")
    print(f"  elements: {index.elements()}")
    print(f"  packed: {packed if packed is not None else 'unknown'}")
    print(f"  if held exactly: {index.exact_bytes()}")
    unknown = index.unknown_types()
    if unknown:
        print(f"  types with no known size: {unknown}")
    for prefix in sorted({name.split(".", 1)[0] for name in index.names()}):
        indices = index.block_indices(prefix)
        if indices:
            print(f"  {prefix}: {len(indices)} blocks")


if __name__ == "__main__":
    raise SystemExit(main())
