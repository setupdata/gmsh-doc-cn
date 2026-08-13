#!/usr/bin/env python3
import sys

from gmsh_doc_cn.cli import main


if __name__ == "__main__":
    main(["baseline", *sys.argv[1:]])
