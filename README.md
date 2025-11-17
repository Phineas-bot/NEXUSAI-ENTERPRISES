# NEXUSAI CloudSim

A lightweight simulator that models storage nodes, their network interconnects, and a discrete-event engine for testing bandwidth-aware transfers.

## Key Components

- **Simulator**: Drives discrete events and enforces execution order based on absolute time.
- **StorageVirtualNetwork**: Shares link bandwidth across concurrent transfers using a configurable tick interval so chunks progress fairly.
- **StorageVirtualNode**: Captures compute, memory, storage, and link characteristics for each simulated endpoint.

## Running the Test Suite

Use the existing virtual environment and execute the Pytest suite to validate the simulator and bandwidth logic:

```powershell
cd "C:/Users/USER PRO/nexusAI/NEXUSAI-ENTERPRISES"
.venv\Scripts\python.exe -m pytest
```

The suite currently includes:

- `tests/test_simulator.py`: Ensures the event scheduler respects absolute times and priority ordering.
- `tests/test_storage_network.py`: Verifies that concurrent transfers share bandwidth (each completes within 10% of the other) and run slower than a solo transfer.

## Bandwidth-Sharing Expectations

- When a link has multiple active transfers, each transfer receives an equal share of the link bandwidth during every tick.
- Transfers that cannot obtain bandwidth fail fast rather than stalling indefinitely.
- Regression tests assert both the slowdown (relative to single-transfer runs) and fairness between concurrent transfers. Adjust the tick interval or tolerances if you change the sharing algorithm.
