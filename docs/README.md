# Documentation

Welcome to the deephaven-questdb project documentation!

## Current Status

- **[Current Status](CURRENT_STATUS.md)** - Latest system state, architecture, and metrics

## Technical Documentation

- **[Orderbooks Implementation](orderbooks.md)** - How orderbooks work with 2D arrays
- **[Verification Guide](verification/end-to-end-verification.md)** - Testing and validation procedures

## Change History

See [history/](history/) for detailed migration and refactoring logs:

### Recent Work (December 2024)
- **[SDK Migration](history/2024-12-sdk-migration/)** - Migrated to official QuestDB Python SDK
- **[Refactoring](history/2024-12-refactoring/)** - Code organization and cleanup

All changes are production-ready with zero breaking changes.

## Quick Links

- **[Main README](../README.md)** - Project overview and quick start
- **[Tests](../docker/cryptofeed/tests/)** - Test suite documentation
- **[Validation Scripts](../validation/)** - Data validation utilities
- **[Makefile](../Makefile)** - Common commands reference

## Structure

```
docs/
├── README.md                       # This file
├── CURRENT_STATUS.md               # System status and architecture
├── DEEPHAVEN_BUG_REPORT.md         # Known issues
├── orderbooks.md                   # Technical guide
├── history/                        # Change history
│   ├── 2024-12-refactoring/       # Refactoring logs
│   └── 2024-12-sdk-migration/     # SDK migration logs
└── verification/                   # Testing documentation
    └── end-to-end-verification.md
```

## Getting Help

1. Check [CURRENT_STATUS.md](CURRENT_STATUS.md) for system overview
2. Review [orderbooks.md](orderbooks.md) for technical details
3. See [history/](history/) for change documentation

## Contributing

When making changes:
1. Update relevant documentation
2. Add to [history/](history/) if significant
3. Update [CURRENT_STATUS.md](CURRENT_STATUS.md) if architecture changes
4. Run tests: `make test` or `cd ../docker/cryptofeed/tests && ./run_tests.sh`
