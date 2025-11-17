from storage_virtual_network import StorageVirtualNetwork
from storage_virtual_node import StorageVirtualNode
from simulator import Simulator


def log_event(event):
    event_type = event.get("type", "unknown")
    time_stamp = event.get("time", 0.0)
    details = {k: v for k, v in event.items() if k not in {"type", "time"}}
    print(f"[{time_stamp:0.2f}s] {event_type}: {details}")


def main():
    simulator = Simulator()
    network = StorageVirtualNetwork(simulator)
    network.register_observer(log_event)

    node1 = StorageVirtualNode("node1", cpu_capacity=4, memory_capacity=16, storage_capacity=500, bandwidth=1000)
    node2 = StorageVirtualNode("node2", cpu_capacity=8, memory_capacity=32, storage_capacity=1000, bandwidth=2000)

    network.add_node(node1)
    network.add_node(node2)

    network.connect_nodes("node1", "node2", bandwidth=1000)

    transfer = network.initiate_file_transfer(
        source_node_id="node1",
        target_node_id="node2",
        file_name="large_dataset.zip",
        file_size=100 * 1024 * 1024,  # 100MB
    )

    if not transfer:
        print("Failed to start transfer. Check storage capacity and bandwidth.")
        return

    print(f"Transfer initiated: {transfer.file_id}")

    simulator.run()

    duration = (transfer.completed_at or simulator.now) - transfer.created_at
    node2_storage = node2.get_storage_utilization()

    print("Simulation finished.")
    print(f"Simulated transfer duration: {duration:.2f}s")
    print(f"Chunks transferred: {len(transfer.chunks)}")
    print(f"Node2 storage utilization: {node2_storage['utilization_percent']:.2f}%")


if __name__ == "__main__":
    main()