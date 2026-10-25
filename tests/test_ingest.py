import unittest

import helpers  # noqa: F401
from toolratchet import diff as D
from toolratchet.ingest import IngestError, parse_surface

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}


class TestFormats(unittest.TestCase):
    def test_mcp_result(self):
        s = parse_surface({"tools": [{"name": "f", "description": "d", "inputSchema": SCHEMA}]})
        self.assertEqual(s.tools[0].input_schema, SCHEMA)

    def test_json_rpc_wrapper(self):
        s = parse_surface({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "f", "inputSchema": SCHEMA}]}})
        self.assertEqual(s.tools[0].name, "f")

    def test_openai_chat(self):
        s = parse_surface([{"type": "function", "function": {"name": "f", "description": "d", "parameters": SCHEMA}}])
        self.assertEqual((s.tools[0].name, s.tools[0].input_schema), ("f", SCHEMA))

    def test_openai_responses_flat(self):
        s = parse_surface([{"type": "function", "name": "f", "description": "d", "parameters": SCHEMA}])
        self.assertEqual(s.tools[0].input_schema, SCHEMA)

    def test_anthropic(self):
        s = parse_surface([{"name": "f", "description": "d", "input_schema": SCHEMA}])
        self.assertEqual(s.tools[0].input_schema, SCHEMA)

    def test_gemini_types_are_lowercased(self):
        s = parse_surface(
            [{"functionDeclarations": [{"name": "f", "parameters": {"type": "OBJECT", "properties": {"q": {"type": "STRING"}}}}]}]
        )
        self.assertEqual(s.tools[0].input_schema["type"], "object")
        self.assertEqual(s.tools[0].input_schema["properties"]["q"]["type"], "string")

    def test_property_named_type_is_untouched(self):
        s = parse_surface([{"name": "f", "parameters": {"type": "OBJECT", "properties": {"type": {"type": "STRING"}}}}])
        self.assertIn("type", s.tools[0].input_schema["properties"])
        self.assertEqual(s.tools[0].input_schema["properties"]["type"]["type"], "string")

    def test_lock_file_with_server(self):
        s = parse_surface({"toolratchet": 1, "server": {"name": "x", "version": "1.2.3"}, "tools": [{"name": "f", "inputSchema": SCHEMA}]})
        self.assertEqual((s.server["name"], s.version), ("x", "1.2.3"))

    def test_annotations_title_split(self):
        s = parse_surface([{"name": "f", "inputSchema": SCHEMA, "annotations": {"title": "Fetch", "readOnlyHint": True}}])
        self.assertEqual(s.tools[0].title, "Fetch")
        self.assertEqual(s.tools[0].annotations, {"readOnlyHint": True})

    def test_same_tool_in_two_vendor_formats_has_no_diff(self):
        mcp = parse_surface({"tools": [{"name": "f", "description": "d", "inputSchema": SCHEMA}]})
        oai = parse_surface([{"type": "function", "function": {"name": "f", "description": "d", "parameters": SCHEMA}}])
        self.assertEqual(D.compare(mcp, oai), [])

    def test_errors(self):
        with self.assertRaises(IngestError):
            parse_surface({"tools": [{"name": "a"}, {"name": "a"}]})
        with self.assertRaises(IngestError):
            parse_surface({"tools": [{"description": "no name"}]})
        with self.assertRaises(IngestError):
            parse_surface({"nope": 1})
        with self.assertRaises(IngestError):
            parse_surface("text")


if __name__ == "__main__":
    unittest.main()
