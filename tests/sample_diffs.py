MODIFIED = """diff --git a/app/utils.py b/app/utils.py
index 1111111..2222222 100644
--- a/app/utils.py
+++ b/app/utils.py
@@ -1,4 +1,5 @@
 import os
-def load(path):
+def load(path, mode="r"):
+    # read the whole file
     return open(path).read()
 
"""

ADDED = """diff --git a/app/new.py b/app/new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/app/new.py
@@ -0,0 +1,2 @@
+def hello():
+    return "hi"
"""

DELETED = """diff --git a/old.py b/old.py
deleted file mode 100644
index 4444444..0000000
--- a/old.py
+++ /dev/null
@@ -1,1 +0,0 @@
-print("bye")
"""

RENAMED = """diff --git a/a.py b/b.py
similarity index 90%
rename from a.py
rename to b.py
index 5555555..6666666 100644
--- a/a.py
+++ b/b.py
@@ -1,1 +1,1 @@
-x = 1
+x = 2
"""

BINARY = """diff --git a/logo.png b/logo.png
new file mode 100644
index 0000000..7777777
Binary files /dev/null and b/logo.png differ
"""

# A removed SQL comment line "-- ..." looks like a "--- " header; the parser
# must still treat it as a removed line because it is inside a hunk.
TRICKY_REMOVED_LINE = """diff --git a/q.sql b/q.sql
index 8888888..9999999 100644
--- a/q.sql
+++ b/q.sql
@@ -1,2 +1,1 @@
--- old comment
 SELECT 1;
"""

MULTI = MODIFIED + ADDED + DELETED + RENAMED + BINARY

# Diff of one new commit on top of MODIFIED: adds line 3 only.
INCREMENTAL = """diff --git a/app/utils.py b/app/utils.py
index 2222222..3333333 100644
--- a/app/utils.py
+++ b/app/utils.py
@@ -2,3 +2,4 @@
 def load(path, mode="r"):
+    # read the whole file
     return open(path).read()
 
"""

# A new commit that adds line 9, which is not part of the PR diff MODIFIED.
INCREMENTAL_OUTSIDE_PR = """diff --git a/app/utils.py b/app/utils.py
index 2222222..4444444 100644
--- a/app/utils.py
+++ b/app/utils.py
@@ -8,1 +8,2 @@
 x = 1
+y = 2
"""
