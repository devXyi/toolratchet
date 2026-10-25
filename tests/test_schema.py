import random
import unittest

import helpers  # noqa: F401
from toolratchet import schema as S

YES, NO, UNKNOWN = S.YES, S.NO, S.UNKNOWN


def verdict(sub, sup):
    issues = S.check_subschema(sub, sup)
    if not issues:
        return YES
    return NO if any(i.definite for i in issues) else UNKNOWN


STR = {"type": "string"}
OBJ = lambda props, **kw: {"type": "object", "properties": props, **kw}  # noqa: E731


class TestProvenCompatible(unittest.TestCase):
    CASES = {
        "identical": (STR, STR),
        "int in number": ({"type": "integer"}, {"type": "number"}),
        "anything in empty": (STR, {}),
        "anything in true": (STR, True),
        "shorter max": ({"type": "string", "maxLength": 10}, {"type": "string", "maxLength": 20}),
        "relaxed min": ({"type": "string", "minLength": 5}, {"type": "string", "minLength": 1}),
        "range inside range": (
            {"type": "integer", "minimum": 5, "maximum": 10},
            {"type": "integer", "minimum": 0, "maximum": 100},
        ),
        "enum subset": ({"enum": ["a", "b"]}, {"enum": ["a", "b", "c"]}),
        "enum in type": ({"enum": ["a"]}, STR),
        "required relaxed": (OBJ({"a": STR}, required=["a"]), OBJ({"a": STR})),
        "optional param added": (OBJ({"a": STR}), OBJ({"a": STR, "b": {"type": "integer"}})),
        "nullable added": (STR, {"anyOf": [STR, {"type": "null"}]}),
        "union type list": ({"type": "string"}, {"type": ["string", "null"]}),
        "fits one branch": (STR, {"anyOf": [{"type": "string", "maxLength": 5}, STR]}),
        "integer exclusive min": ({"type": "integer", "exclusiveMinimum": 0}, {"type": "integer", "minimum": 1}),
        "enum in range": ({"enum": [1, 2]}, {"type": "integer", "minimum": 1, "maximum": 2}),
        "bool as enum": ({"type": "boolean"}, {"enum": [True, False]}),
        "array items widened": (
            {"type": "array", "items": {"type": "integer"}},
            {"type": "array", "items": {"type": "number"}},
        ),
        "multiple of multiple": ({"type": "integer", "multipleOf": 10}, {"type": "integer", "multipleOf": 5}),
        "allOf merged": (
            {"allOf": [{"type": "object", "required": ["a"]}, {"properties": {"a": STR}}]},
            OBJ({"a": STR}, required=["a"]),
        ),
        "const in enum": ({"const": "x"}, {"enum": ["x", "y"]}),
        "ref equals inline": (
            {"$ref": "#/$defs/s", "$defs": {"s": {"type": "string", "maxLength": 3}}},
            {"type": "string", "maxLength": 3},
        ),
    }

    def test_all(self):
        for name, (sub, sup) in self.CASES.items():
            with self.subTest(name):
                self.assertEqual(verdict(sub, sup), YES, S.check_subschema(sub, sup))

    def test_recursive_schema_terminates(self):
        defs = {"N": {"type": "object", "properties": {"child": {"$ref": "#/$defs/N"}}}}
        defs2 = {"N": {"type": "object", "properties": {"child": {"$ref": "#/$defs/N"}, "tag": STR}}}
        sub = {"$ref": "#/$defs/N", "$defs": defs}
        sup = {"$ref": "#/$defs/N", "$defs": defs2}
        self.assertEqual(verdict(sub, sup), YES)


class TestProvenBreaking(unittest.TestCase):
    CASES = {
        "type changed": (STR, {"type": "integer"}),
        "number to integer": ({"type": "number"}, {"type": "integer"}),
        "max length added": (STR, {"type": "string", "maxLength": 5}),
        "max length lowered": ({"type": "string", "maxLength": 10}, {"type": "string", "maxLength": 5}),
        "minimum raised": ({"type": "integer", "minimum": 0}, {"type": "integer", "minimum": 1}),
        "minimum added": ({"type": "integer"}, {"type": "integer", "minimum": 0}),
        "enum shrunk": ({"enum": ["a", "b"]}, {"enum": ["a"]}),
        "enum introduced": (STR, {"enum": ["a", "b"]}),
        "required added": (OBJ({"a": {}}), OBJ({"a": {}}, required=["a"])),
        "closed schema": (
            OBJ({"a": STR, "b": STR}),
            OBJ({"a": STR}, additionalProperties=False),
        ),
        "nested type narrowed": (OBJ({"a": STR}), OBJ({"a": {"type": "integer"}})),
        "null no longer allowed": ({"type": ["string", "null"]}, STR),
        "not in any branch": (STR, {"anyOf": [{"type": "integer"}, {"type": "boolean"}]}),
        "unique added": (
            {"type": "array", "items": STR},
            {"type": "array", "items": STR, "uniqueItems": True},
        ),
        "min items raised": (
            {"type": "array", "minItems": 0},
            {"type": "array", "minItems": 2},
        ),
        "pattern added": (STR, {"type": "string", "pattern": "^a"}),
        "multiple added": ({"type": "integer"}, {"type": "integer", "multipleOf": 2}),
        "bool narrowed": ({"type": "boolean"}, {"enum": [True]}),
        "nothing accepted": (STR, False),
        "items narrowed": (
            {"type": "array", "items": {"type": "number"}},
            {"type": "array", "items": {"type": "integer"}},
        ),
    }

    def test_all(self):
        for name, (sub, sup) in self.CASES.items():
            with self.subTest(name):
                self.assertEqual(verdict(sub, sup), NO, S.check_subschema(sub, sup))


class TestUnknown(unittest.TestCase):
    CASES = {
        "pattern changed": (
            {"type": "string", "pattern": "^a"},
            {"type": "string", "pattern": "^b"},
        ),
        "format changed": (
            {"type": "string", "format": "uri"},
            {"type": "string", "format": "email"},
        ),
        "format added": (STR, {"type": "string", "format": "uri"}),
        "not keyword": (STR, {"type": "string", "not": {"enum": ["x"]}}),
        "if/then": (STR, {"type": "string", "if": {"minLength": 3}, "then": {"maxLength": 9}}),
        "pattern properties": (
            OBJ({}),
            OBJ({}, patternProperties={"^x": STR}),
        ),
        "dangling ref": ({"$ref": "#/nope"}, STR),
        "enum needs format check": ({"enum": ["http://x"]}, {"type": "string", "format": "uri"}),
        "tuple items differ": (
            {"type": "array", "prefixItems": [STR]},
            {"type": "array", "prefixItems": [{"type": "integer"}]},
        ),
        "union not provably covered": (
            {"type": "string", "maxLength": 3},
            {"anyOf": [{"type": "string", "maxLength": 1}, {"type": "string", "minLength": 2}]},
        ),
    }

    def test_all(self):
        for name, (sub, sup) in self.CASES.items():
            with self.subTest(name):
                self.assertEqual(verdict(sub, sup), UNKNOWN, S.check_subschema(sub, sup))


class TestValidate(unittest.TestCase):
    def test_basics(self):
        self.assertEqual(S.validate("a", {"type": "string", "maxLength": 3}), YES)
        self.assertEqual(S.validate("abcd", {"type": "string", "maxLength": 3}), NO)
        self.assertEqual(S.validate(True, {"type": "integer"}), NO)
        self.assertEqual(S.validate(1, {"enum": [True]}), NO)
        self.assertEqual(S.validate({"a": 1}, OBJ({"a": {"type": "integer"}}, required=["a"])), YES)
        self.assertEqual(S.validate({}, OBJ({}, required=["a"])), NO)
        self.assertEqual(S.validate("x", {"type": "string", "format": "uri"}), UNKNOWN)
        self.assertEqual(S.validate(3, {"oneOf": [{"type": "integer"}, {"type": "number"}]}), NO)


# --- randomized soundness: a proven subschema must never admit a value the superschema rejects

POOL = [
    None, True, False, 0, 1, 2, 5, 10, -1, 1.5, 100,
    "", "a", "ab", "abc", "b", "hello",
    [], [1], ["a", "b"], [1, "a"], [1, 1],
    {}, {"a": 1}, {"a": "x"}, {"a": 1, "b": 2}, {"b": "x"}, {"a": None},
]


def rand_schema(rng, depth=0):
    s = {}
    if rng.random() < 0.8:
        s["type"] = rng.choice(
            ["string", "integer", "number", "boolean", "null", "object", "array", ["string", "null"], ["integer", "string"]]
        )
    if rng.random() < 0.2:
        s["enum"] = rng.sample([None, True, 0, 1, 5, "a", "b", "abc"], k=rng.randint(1, 3))
    if rng.random() < 0.3:
        s["minimum"] = rng.choice([0, 1, 5])
    if rng.random() < 0.3:
        s["maximum"] = rng.choice([5, 10, 100])
    if rng.random() < 0.3:
        s["minLength"] = rng.choice([0, 1, 2])
    if rng.random() < 0.3:
        s["maxLength"] = rng.choice([1, 3, 10])
    if rng.random() < 0.15:
        s["pattern"] = rng.choice(["^a", "^b", "c$"])
    if rng.random() < 0.15:
        s["multipleOf"] = rng.choice([1, 2, 5])
    if rng.random() < 0.3:
        s["required"] = rng.sample(["a", "b"], k=rng.randint(0, 2))
    if rng.random() < 0.3 and depth < 2:
        s["properties"] = {k: rand_schema(rng, depth + 1) for k in rng.sample(["a", "b"], k=rng.randint(1, 2))}
    if rng.random() < 0.1:
        s["additionalProperties"] = False
    if rng.random() < 0.25 and depth < 2:
        s["items"] = rand_schema(rng, depth + 1)
    if rng.random() < 0.15:
        s["minItems"] = rng.choice([0, 1, 2])
    if rng.random() < 0.1:
        s["uniqueItems"] = True
    if rng.random() < 0.12 and depth < 2:
        s["anyOf"] = [rand_schema(rng, depth + 1), rand_schema(rng, depth + 1)]
    return s


def closed_world(s):
    """The documented practical assumption: callers only send properties the old schema declares."""
    if not isinstance(s, dict):
        return s
    out = dict(s)
    if "additionalProperties" not in out:
        out["additionalProperties"] = False
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: closed_world(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = closed_world(out["items"])
    if isinstance(out.get("anyOf"), list):
        out["anyOf"] = [closed_world(x) for x in out["anyOf"]]
    return out


class TestSoundness(unittest.TestCase):
    def test_proven_never_admits_extra_values(self):
        rng = random.Random(20261024)
        proven = 0
        for _ in range(6000):
            a, b = rand_schema(rng), rand_schema(rng)
            if S.check_subschema(a, b):
                continue
            proven += 1
            ca = closed_world(a)
            for v in POOL:
                if S.validate(v, ca) == YES and S.validate(v, b) == NO:
                    self.fail(f"unsound: {a} claimed subschema of {b}, but {v!r} is valid in the first only")
        self.assertGreater(proven, 200, "generator produced too few proven pairs to be meaningful")


if __name__ == "__main__":
    unittest.main()
