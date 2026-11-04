"""Code harness generators for platform-specific execution."""


def wrap_leetcode_code(solution_code: str, method_name: str | None = None) -> str:
    """Generate a test driver for a LeetCode Solution class.

    If the code does not already contain a stdin driver, append an intelligent
    entrypoint that parses JSON/arguments from input() and calls Solution.
    Does not use forbidden modules like sys.
    """
    if "if __name__ == '__main__':" in solution_code:
        return solution_code

    driver_lines = [
        "import json",
        "",
        "if __name__ == '__main__':",
        "    lines = []",
        "    while True:",
        "        try:",
        "            line = input()",
        "            if line.strip():",
        "                lines.append(line.strip())",
        "        except EOFError:",
        "            break",
        "",
        "    if lines:",
        "        sol = Solution()",
    ]

    if method_name:
        driver_lines.extend(
            [
                f"        func = getattr(sol, '{method_name}', None)",
                "        if func is None:",
                "            methods = [m for m in dir(sol) if not m.startswith('_') and callable(getattr(sol, m))]",
                "            func = getattr(sol, methods[0]) if methods else None",
            ]
        )
    else:
        driver_lines.extend(
            [
                "        methods = [m for m in dir(sol) if not m.startswith('_') and callable(getattr(sol, m))]",
                "        func = getattr(sol, methods[0]) if methods else None",
            ]
        )

    driver_lines.extend(
        [
            "        if func is not None:",
            "            args = []",
            "            for line in lines:",
            "                try:",
            "                    args.append(json.loads(line))",
            "                except Exception:",
            "                    if line.isdigit():",
            "                        args.append(int(line))",
            "                    else:",
            "                        args.append(line)",
            "            res = func(*args)",
            "            if res is not None:",
            "                if isinstance(res, (dict, list, bool)):",
            "                    print(json.dumps(res))",
            "                else:",
            "                    print(res)",
        ]
    )

    driver = "\n".join(driver_lines)
    return f"{solution_code}\n\n{driver}\n"
