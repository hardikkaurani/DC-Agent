"""Code harness generators for platform-specific execution."""

import textwrap


def wrap_leetcode_code(solution_code: str, method_name: str | None = None) -> str:
    """Generate a test driver for a LeetCode Solution class.

    If the code does not already contain a stdin driver, append an intelligent
    entrypoint that parses JSON/arguments from sys.stdin and calls Solution.
    """
    if "if __name__ == '__main__':" in solution_code or "sys.stdin" in solution_code:
        return solution_code

    driver = textwrap.dedent("""
    import sys
    import json

    if __name__ == '__main__':
        lines = [line.strip() for line in sys.stdin.readlines() if line.strip()]
        if not lines:
            sys.exit(0)

        sol = Solution()
        # Find the primary public method on Solution
        methods = [m for m in dir(sol) if not m.startswith('_') and callable(getattr(sol, m))]
        if not methods:
            sys.exit(0)

        func = getattr(sol, methods[0])
        args = []
        for line in lines:
            try:
                args.append(json.loads(line))
            except Exception:
                # Fallback to string or int
                if line.isdigit():
                    args.append(int(line))
                else:
                    args.append(line)

        try:
            res = func(*args)
            if res is not None:
                if isinstance(res, (dict, list, bool)):
                    print(json.dumps(res))
                else:
                    print(res)
        except Exception as e:
            sys.stderr.write(f"Driver execution error: {e}\\n")
            sys.exit(1)
    """)

    return f"{solution_code}\n\n{driver}"
