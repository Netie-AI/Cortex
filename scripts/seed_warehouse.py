"""Backward-compat wrapper — prefer: python -m CortexOS.dms.seed_demo"""

from CortexOS.dms.seed_demo import main, seed_demo_warehouse

if __name__ == "__main__":
    main()
