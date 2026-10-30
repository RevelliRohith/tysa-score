"""
Refactored Concurrency Suite:
Delegates directly to TestPostgreSQLProductionConcurrency which invokes the actual
production services (ChampionshipService) rather than duplicating transaction logic.
"""
from .test_postgresql_production_concurrency import TestPostgreSQLProductionConcurrency

__all__ = ['TestPostgreSQLProductionConcurrency']
