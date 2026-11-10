"""Recipes: the variant stages a stack builds and their parameters
(strand stage-graph, design §6 and §10b, step 3).

A recipe is a committed JSON file, ``customizations/recipes/<name>.json``::

    {
      "name": "sira-taxonomy",
      "stages": ["taxonomy", "enrich"],
      "params": {
        "taxonomy": {"generation": 1},
        "enrich":   {"prompt_scheme": "<scheme>", "taxonomy": true, "generation": 1}
      },
      "serve": {"mode": "sira", "units": ["cells", "trees", "history"]}
    }

It names variant stages only; the corpus prefix is the same for every
stack (D-DRAFT-1). ``cycle.sh start`` freezes the stack's recipe into the
build as ``RECIPE.json``; the driver, the pipeline and publish read it
there. A ``model`` parameter, when present, overrides the model of the
container's env file for that stage; endpoints and keys stay in the env
files. ``generation`` (default 1) is the one way to recompute a variant
stage whose inputs did not change.

Standard library only: the host-side cycle driver and runner import this
module with the host's python3. CLI::

    python3 -m core.src.pipeline.recipes check <file>       # validate; prints a summary
    python3 -m core.src.pipeline.recipes get <file> <key>   # name, stages, has.<stage>,
                                                            # <stage>.<param>, enrich.taxonomy
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

RECIPES_DIR = Path("customizations") / "recipes"
RECIPE_FILE = "RECIPE.json"

#: The variant stages the driver runs today, in run order.
DRIVER_STAGES = ("taxonomy", "enrich")
#: Variant stages with a place reserved and no driver yet (step 4).
RESERVED_STAGES = ("graph", "vectorstore", "eval")
#: The parameters each stage takes, with their types.
PARAMS: dict[str, dict[str, type]] = {
    "taxonomy": {"generation": int, "model": str},
    "enrich": {"generation": int, "model": str, "prompt_scheme": str, "taxonomy": bool},
}
SERVE_MODES = ("sira",)
SERVE_UNITS = ("cells", "trees", "history")
_TOP_KEYS = {"name", "stages", "params", "serve", "note"}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class RecipeError(ValueError):
    """A recipe that cannot be read or is not valid (RCP-E001 / RCP-E002)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


@dataclass(frozen=True)
class Recipe:
    """A validated recipe."""

    name: str
    stages: tuple[str, ...]
    params: dict = field(default_factory=dict)
    serve: dict = field(default_factory=dict)
    note: str = ""

    def has(self, stage: str) -> bool:
        return stage in self.stages

    def stage_params(self, stage: str) -> dict:
        """The stage's parameters with `generation` filled in (default 1);
        empty for a stage the recipe does not run."""
        if not self.has(stage):
            return {}
        out = {"generation": 1}
        out.update(self.params.get(stage, {}))
        return out

    def param(self, stage: str, key: str, default=None):
        return self.stage_params(stage).get(key, default)

    @property
    def enrich_uses_taxonomy(self) -> bool:
        """Whether enrich reads the cell's taxonomy: its `taxonomy` param,
        else whether the recipe runs taxonomy."""
        return bool(self.param("enrich", "taxonomy", self.has("taxonomy")))

    @property
    def prompt_scheme(self) -> str:
        return str(self.param("enrich", "prompt_scheme", "") or "")

    def to_dict(self) -> dict:
        d = {"name": self.name, "stages": list(self.stages), "params": self.params,
             "serve": self.serve}
        if self.note:
            d["note"] = self.note
        return d

    @property
    def digest(self) -> str:
        """sha256 of the canonical JSON, 16 hex: two recipes with one
        digest build alike."""
        body = self.to_dict()
        body.pop("note", None)
        return hashlib.sha256(_canonical(body)).hexdigest()[:16]


def _bad(message: str) -> RecipeError:
    return RecipeError("RCP-E002", message)


def parse(data, name_hint: str | None = None) -> Recipe:
    """Validate `data` (the parsed JSON) and return the `Recipe`.
    `name_hint` is the file's stem; the recipe's name must equal it."""
    if not isinstance(data, dict):
        raise _bad("a recipe is a JSON object")
    extra = set(data) - _TOP_KEYS
    if extra:
        raise _bad(f"unknown key(s): {', '.join(sorted(extra))}")
    name = data.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise _bad("name: lower-case letters, digits and dashes")
    if name_hint is not None and name != name_hint:
        raise _bad(f"name {name!r} differs from the file name {name_hint!r}")

    stages = data.get("stages")
    if not isinstance(stages, list) or not stages or not all(isinstance(s, str) for s in stages):
        raise _bad("stages: a non-empty list of stage names")
    if len(set(stages)) != len(stages):
        raise _bad("stages: listed twice")
    for s in stages:
        if s in RESERVED_STAGES:
            raise _bad(f"stage {s!r} has no driver yet (step 4 of the stage graph)")
        if s not in DRIVER_STAGES:
            raise _bad(f"stage {s!r} is not a variant stage a recipe can name "
                       f"({', '.join(DRIVER_STAGES)}); the corpus prefix is not chosen")
    ordered = tuple(s for s in DRIVER_STAGES if s in stages)

    params = data.get("params", {})
    if not isinstance(params, dict):
        raise _bad("params: an object keyed by stage")
    clean: dict[str, dict] = {}
    for stage, ps in params.items():
        if stage not in ordered:
            raise _bad(f"params for {stage!r}, which the recipe does not run")
        if not isinstance(ps, dict):
            raise _bad(f"params.{stage}: an object")
        for key, value in ps.items():
            want = PARAMS[stage].get(key)
            if want is None:
                raise _bad(f"params.{stage}.{key}: unknown (allowed: {', '.join(sorted(PARAMS[stage]))})")
            if want is int and (isinstance(value, bool) or not isinstance(value, int)):
                raise _bad(f"params.{stage}.{key}: an integer")
            if want is not int and not isinstance(value, want):
                raise _bad(f"params.{stage}.{key}: a {want.__name__}")
            if key == "generation" and value < 1:
                raise _bad(f"params.{stage}.generation: 1 or more")
            if want is str and not value.strip():
                raise _bad(f"params.{stage}.{key}: empty (leave it out instead)")
        clean[stage] = dict(sorted(ps.items()))
    if clean.get("enrich", {}).get("taxonomy") and "taxonomy" not in ordered:
        raise _bad("params.enrich.taxonomy is true but the recipe does not run taxonomy")

    serve = data.get("serve", {})
    if not isinstance(serve, dict) or set(serve) - {"mode", "units"}:
        raise _bad("serve: an object with mode and units")
    mode = serve.get("mode", "sira")
    if mode not in SERVE_MODES:
        raise _bad(f"serve.mode {mode!r}: one of {', '.join(SERVE_MODES)}")
    units = serve.get("units", list(SERVE_UNITS))
    if (not isinstance(units, list) or not units
            or any(u not in SERVE_UNITS for u in units) or len(set(units)) != len(units)):
        raise _bad(f"serve.units: a non-empty subset of {', '.join(SERVE_UNITS)}")

    note = data.get("note", "")
    if not isinstance(note, str):
        raise _bad("note: a string")
    return Recipe(name=name, stages=ordered, params=clean,
                  serve={"mode": mode, "units": [u for u in SERVE_UNITS if u in units]}, note=note)


def load(path: str | Path, check_name: bool = False) -> Recipe:
    """Read and validate a recipe file. `check_name` requires the name to
    equal the file's stem (a file under ``customizations/recipes``; a
    frozen ``RECIPE.json`` keeps its recipe's name)."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RecipeError("RCP-E001", f"recipe unreadable: {p.name} ({e})") from e
    return parse(data, name_hint=p.stem if check_name else None)


def recipe_path(repo_root: str | Path, name: str) -> Path:
    return Path(repo_root) / RECIPES_DIR / f"{name}.json"


def load_frozen(build_dir: str | Path) -> Recipe | None:
    """The recipe frozen in a build (``<build>/RECIPE.json``), or None."""
    p = Path(build_dir) / RECIPE_FILE
    return load(p) if p.is_file() else None


def get(recipe: Recipe, key: str) -> str:
    """One value as text for the shell: ``name``, ``digest``, ``stages``
    (space-separated), ``has.<stage>`` (``1``/``0``), ``enrich.taxonomy``
    (``1``/``0``), ``prompt_scheme``, ``<stage>.<param>`` (``''`` when unset)."""
    if key == "name":
        return recipe.name
    if key == "digest":
        return recipe.digest
    if key == "stages":
        return " ".join(recipe.stages)
    if key == "prompt_scheme":
        return recipe.prompt_scheme
    if key == "enrich.taxonomy":
        return "1" if recipe.enrich_uses_taxonomy else "0"
    if key.startswith("has."):
        return "1" if recipe.has(key[4:]) else "0"
    if "." in key:
        stage, param = key.split(".", 1)
        value = recipe.param(stage, param, "")
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)
    raise RecipeError("RCP-E002", f"unknown key {key!r}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] not in ("check", "get") or (argv[0] == "get" and len(argv) != 3):
        print(__doc__.split("CLI::", 1)[1].rstrip(), file=sys.stderr)
        return 2
    try:
        path = Path(argv[1])
        recipe = load(path, check_name=path.name != RECIPE_FILE)
        if argv[0] == "check":
            print(f"recipe {recipe.name}: stages={','.join(recipe.stages)} "
                  f"enrich.taxonomy={get(recipe, 'enrich.taxonomy')} "
                  f"scheme={recipe.prompt_scheme or '-'} digest={recipe.digest}")
        else:
            print(get(recipe, argv[2]))
    except RecipeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
