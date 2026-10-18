from nitpick.rag.diff_utils import changed_paths, extract_changed_symbols, parse_unified_diff


def test_parse_basic_diff(pr_diff):
    files = parse_unified_diff(pr_diff)
    assert len(files) == 1
    f = files[0]
    assert f.path == "orders.py"
    h = f.hunks[0]
    assert h.new_start == 3
    assert h.header_context == "TAX_RATE = 0.08"
    assert "def calculate_total(items, discount, currency):" in h.added
    assert "def calculate_total(items, discount):" in h.removed


def test_symbols_from_changed_lines(pr_diff):
    assert extract_changed_symbols(parse_unified_diff(pr_diff)) == ["calculate_total"]


def test_symbol_from_hunk_header_and_stoplist():
    diff = (
        "--- a/svc.py\n+++ b/svc.py\n"
        "@@ -10 +10 @@ def load_user_profile(uid):\n"
        "-    return db.get(uid)\n+    return db.get(str(uid))\n"
        "@@ -30 +30 @@ class Thing:\n"
        "-    def __init__(self):\n+    def __init__(self, x):\n"
    )
    syms = extract_changed_symbols(parse_unified_diff(diff))
    assert syms == ["load_user_profile", "Thing"]  # __init__ filtered out


def test_added_and_deleted_files_and_js_arrow():
    diff = (
        "diff --git a/new.ts b/new.ts\nnew file mode 100644\n--- /dev/null\n+++ b/new.ts\n"
        "@@ -0,0 +1,2 @@\n+export const fetchOrders = async (id) => {\n+}\n"
        "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-def legacy_helper():\n-    pass\n"
    )
    files = parse_unified_diff(diff)
    assert [(f.old_path, f.new_path) for f in files] == [(None, "new.ts"), ("old.py", None)]
    assert changed_paths(files) == {"new.ts", "old.py"}
    assert extract_changed_symbols(files) == ["fetchOrders", "legacy_helper"]


def test_rename_counts_both_paths():
    diff = "--- a/a.py\n+++ b/b.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    assert changed_paths(parse_unified_diff(diff)) == {"a.py", "b.py"}


def test_lines_starting_with_dashes_inside_hunk_are_content():
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n--- not a header\n+++ also content\n"
    files = parse_unified_diff(diff)
    assert len(files) == 1
    assert files[0].hunks[0].removed == ["-- not a header"]
    assert files[0].hunks[0].added == ["++ also content"]


def test_no_newline_marker_and_multiple_files():
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n\\ No newline at end of file\n"
        "+x = 2\n\\ No newline at end of file\n"
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,2 @@\n ctx\n-y = 1\n+y = 2\n"
    )
    files = parse_unified_diff(diff)
    assert [f.path for f in files] == ["a.py", "b.py"]
    assert files[0].hunks[0].removed == ["x = 1"] and files[0].hunks[0].added == ["x = 2"]
    assert files[1].hunks[0].added == ["y = 2"]


def test_body_only_change_finds_enclosing_def_in_context_lines():
    # Short file: the hunk starts at line 1, so git puts no def in the @@ header.
    diff = (
        "--- a/app/pricing.py\n+++ b/app/pricing.py\n@@ -1,3 +1,5 @@\n"
        " def apply_discount(price, discount):\n"
        "-    return price * (1 - discount / 100)\n"
        "+    if not 0 <= discount <= 1:\n"
        "+        raise ValueError(\"discount must be a fraction\")\n"
        "+    return price * (1 - discount)\n"
    )
    assert extract_changed_symbols(parse_unified_diff(diff)) == ["apply_discount"]


def test_top_level_change_after_a_function_is_not_attributed_to_it():
    diff = (
        "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n"
        " def helper_func():\n"
        "     return 1\n"
        "-LIMIT = 5\n"
        "+LIMIT = 6\n"
    )
    assert extract_changed_symbols(parse_unified_diff(diff)) == []


def test_used_names_calls_and_imports():
    from nitpick.rag.diff_utils import extract_used_names
    diff = (
        "--- a/app/catalog.py\n+++ b/app/catalog.py\n@@ -1,1 +1,6 @@\n"
        "-pass\n"
        "+from app.storage import load_items, save_items as save\n"
        "+def sorted_items():\n"
        "+    items = load_items()\n"
        "+    print(len(items))\n"
        "+    self.refresh_cache()\n"
        "+    return sorted_items_helper(items)\n"
    )
    names = extract_used_names(parse_unified_diff(diff))
    # builtins, method calls and names defined in the diff are left out
    assert names == ["load_items", "save_items", "sorted_items_helper"]
