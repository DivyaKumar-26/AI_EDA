"""Small validated circuit IR that keeps netlist generation deterministic."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CircuitElement:
    reference: str
    nodes: tuple[str, ...]
    body: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", self.reference):
            raise ValueError(f"invalid component reference: {self.reference!r}")
        if len(self.nodes) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_.$-]+", node) for node in self.nodes):
            raise ValueError(f"component {self.reference} has invalid or missing nodes")
        if not self.body or "\n" in self.body or "\r" in self.body:
            raise ValueError(f"component {self.reference} must have a single-line value or model")

    @classmethod
    def from_statement(cls, line: str) -> "CircuitElement":
        tokens = line.split()
        if len(tokens) < 4:
            raise ValueError(f"invalid SPICE component statement: {line!r}")
        reference = tokens[0]
        kind = reference[0].upper()
        pin_counts = {"R": 2, "C": 2, "L": 2, "V": 2, "I": 2, "D": 2, "Q": 3,
                      "M": 4, "B": 2, "E": 4, "G": 4, "F": 2, "H": 2, "S": 4, "T": 4}
        if kind == "X":
            model_index = next((i for i in range(len(tokens) - 1, 0, -1) if "=" not in tokens[i]), None)
            if model_index is None or model_index < 3:
                raise ValueError(f"invalid subcircuit instance statement: {line!r}")
            node_count = model_index - 1
        else:
            node_count = pin_counts.get(kind)
            if node_count is None:
                raise ValueError(f"unsupported SPICE component kind {kind!r}")
        if len(tokens) <= node_count + 1:
            raise ValueError(f"component {reference} has too few pins or no value/model")
        return cls(reference, tuple(tokens[1 : 1 + node_count]), " ".join(tokens[1 + node_count :]))

    @property
    def statement(self) -> str:
        return " ".join((self.reference, *self.nodes, self.body))


@dataclass(frozen=True)
class CircuitIR:
    """Canonical SPICE representation split into components and definitions."""

    title: str
    elements: tuple[CircuitElement, ...]
    models: tuple[str, ...] = ()
    subcircuits: tuple[str, ...] = ()
    directives: tuple[str, ...] = ()
    comments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        refs = [element.reference.casefold() for element in self.elements]
        if len(refs) != len(set(refs)):
            raise ValueError("circuit component references must be unique")
        if not self.title.strip():
            raise ValueError("circuit title must not be empty")
        for line in (*self.models, *self.subcircuits, *self.directives, *self.comments):
            if "\r" in line or ("\n" in line and line not in self.subcircuits):
                raise ValueError("IR lines must not contain embedded line breaks")

    @classmethod
    def from_spice_text(cls, source: str, title: str | None = None) -> "CircuitIR":
        comments: list[str] = []
        elements: list[CircuitElement] = []
        models: list[str] = []
        subcircuits: list[str] = []
        directives: list[str] = []
        lines = source.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            index += 1
            if not line:
                continue
            if line.startswith("*"):
                comments.append(line)
            elif line.casefold() == ".end":
                continue
            elif line.casefold().startswith(".subckt "):
                block = [line]
                while index < len(lines):
                    block.append(lines[index].strip())
                    done = lines[index].strip().casefold().startswith(".ends")
                    index += 1
                    if done:
                        break
                if not block[-1].casefold().startswith(".ends"):
                    raise ValueError("unterminated .subckt definition")
                subcircuits.append("\n".join(block))
            elif line.casefold().startswith(".model "):
                models.append(line)
            elif line.startswith("."):
                directives.append(line)
            else:
                elements.append(CircuitElement.from_statement(line))
        circuit_title = title or next((comment.lstrip("* ") for comment in comments if comment.strip("* ").strip()), "Circuit")
        return cls(circuit_title, tuple(elements), tuple(models), tuple(subcircuits), tuple(directives), tuple(comments))

    def render(self) -> str:
        """Render comments, elements, model definitions, and analyses in fixed order."""
        comment_lines = self.comments or (f"* {self.title}",)
        parts = [*comment_lines, *(element.statement for element in self.elements), *self.models,
                 *self.subcircuits, *self.directives, ".end"]
        return "\n".join(parts) + "\n"

    @property
    def nets(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(node for element in self.elements for node in element.nodes))

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "elements": [{"reference": element.reference, "nodes": list(element.nodes), "body": element.body}
                         for element in self.elements],
            "nets": list(self.nets),
            "models": list(self.models),
            "subcircuits": list(self.subcircuits),
            "directives": list(self.directives),
            "comments": list(self.comments),
        }
