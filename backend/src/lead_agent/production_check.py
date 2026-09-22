from __future__ import annotations

import sys

from pydantic import ValidationError

from lead_agent.settings.config import Settings


def main() -> int:
    try:
        settings = Settings()
        if settings.app_env != "production":
            print("FAIL production-check: APP_ENV must be production")
            return 1
    except ValidationError as error:
        issues = sorted({str(item["msg"]) for item in error.errors(include_input=False)})
        print("FAIL production-check: configuration is unsafe or incomplete")
        for issue in issues:
            print(f"- {issue}")
        return 1
    except Exception:
        # Never print exception text from configuration or deployment factories.
        print("FAIL production-check: configuration could not be loaded")
        return 1
    print("PASS production-check: configuration passed validation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
