import textwrap

from nitpick.rag.chunker import chunk_by_lines, chunk_file


def test_python_split_by_function_and_module_code():
    src = textwrap.dedent('''\
        import os

        X = 1


        @decorator
        def foo():
            return 1


        def bar():
            return 2
        ''')
    chunks = chunk_file("pkg/mod.py", src)
    symbols = [c.symbol for c in chunks]
    assert symbols == ["<module>", "foo", "bar"]
    foo = chunks[1]
    assert foo.text.startswith("@decorator")  # decorator included
    assert (foo.start_line, foo.end_line) == (6, 8)


def test_large_class_is_split_into_methods():
    methods = "\n".join(f"    def m{i}(self):\n" + "        x = 1\n" * 10 for i in range(10))
    src = 'class Big:\n    """doc"""\n    ATTR = 1\n\n' + methods
    chunks = chunk_file("big.py", src, max_lines=40)
    symbols = [c.symbol for c in chunks]
    assert symbols[0] == "Big"                     # class header
    assert "Big.m0" in symbols and "Big.m9" in symbols
    assert "def m0" not in chunks[0].text          # header stops before first method


def test_small_class_is_one_chunk():
    src = "class Small:\n    def a(self):\n        return 1\n"
    chunks = chunk_file("s.py", src)
    assert len(chunks) == 1 and chunks[0].symbol == "Small"


def test_syntax_error_falls_back_to_lines():
    chunks = chunk_file("broken.py", "def oops(:\n    pass\n")
    assert len(chunks) == 1 and chunks[0].symbol is None


def test_non_python_uses_windows_with_overlap():
    src = "\n".join(f"line {i}" for i in range(1, 131))
    chunks = chunk_by_lines("a.js", src, window=60, overlap=10)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 60), (51, 110), (101, 130)]


def test_empty_file_and_windows_paths():
    assert chunk_file("x.py", "   \n") == []
    assert chunk_file("dir\\x.py", "def f():\n    pass\n")[0].path == "dir/x.py"
