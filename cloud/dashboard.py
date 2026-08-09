# cloud/dashboard.py

import json
import time
import os
import threading
from datetime import datetime
from collections import deque
import paho.mqtt.client as mqtt
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.columns import Columns
from rich import box

TOPIC_MEASUREMENTS = "energy-iot/measurements"
TOPIC_STATUS       = "energy-iot/status"
TOPIC_FAULTS       = "energy-iot/faults"

console = Console()


class EnergyDashboard:
    """
    Live terminal dashboard subscribing to MQTT broker.
    Displays real-time energy measurements.
    Shows buffered vs live data.
    Alerts on faults.
    """

    def __init__(
        self,
        broker_host: str = "localhost",
        broker_port: int = 1883
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self._latest: dict = {}
        self._history = deque(maxlen=10)
        self._faults: list = []
        self._status = "unknown"
        self._lock = threading.Lock()
        self._message_count = 0
        self._buffered_count = 0

        self.client = mqtt.Client(client_id="dashboard")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            # Subscribe to all topics
            client.subscribe(TOPIC_MEASUREMENTS)
            client.subscribe(TOPIC_STATUS)
            client.subscribe(TOPIC_FAULTS)
            console.print(
                "[green]Dashboard connected to broker[/green]"
            )

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message."""
        try:
            payload = json.loads(msg.payload.decode())

            with self._lock:
                if msg.topic == TOPIC_MEASUREMENTS:
                    self._latest = payload
                    self._history.appendleft(payload)
                    self._message_count += 1
                    if payload.get("buffered"):
                        self._buffered_count += 1

                elif msg.topic == TOPIC_STATUS:
                    self._status = payload.get("status", "unknown")

                elif msg.topic == TOPIC_FAULTS:
                    fault = payload.get("fault_code", "unknown")
                    self._faults.append({
                        "fault": fault,
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
                    # Keep last 5 faults only
                    self._faults = self._faults[-5:]

        except Exception as e:
            console.print(f"[red]Message parse error: {e}[/red]")

    def _build_display(self) -> str:
        """Build the dashboard display."""
        with self._lock:
            latest = self._latest.copy()
            history = list(self._history)
            faults = list(self._faults)
            status = self._status
            msg_count = self._message_count
            buf_count = self._buffered_count

        if not latest:
            return Panel(
                "[yellow]Waiting for measurements...[/yellow]",
                title="Energy IoT System POC",
                border_style="yellow"
            )

        # Format timestamp
        ts = latest.get("timestamp", 0)
        time_str = datetime.fromtimestamp(ts).strftime(
            "%H:%M:%S"
        ) if ts else "unknown"

        # Device status colour
        status_colour = (
            "green" if status == "online"
            else "red" if status == "offline"
            else "yellow"
        )

        # Build main measurements table
        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
            border_style="blue"
        )
        table.add_column("Measurement", style="bold white", width=25)
        table.add_column("Value", justify="right", width=12)
        table.add_column("Direction", width=20)

        pv   = latest.get("pv_production_w", 0)
        ac   = latest.get("ac_output_w", 0)
        batt = latest.get("battery_power_w", 0)
        grid = latest.get("grid_power_w", 0)
        house = latest.get("house_consumption_w", 0)
        delta = latest.get("timestamp_delta_ms", 0)

        table.add_row(
            "☀  PV Production",
            f"{pv:.1f} W",
            "[yellow]Solar panels[/yellow]"
        )
        table.add_row(
            "⚡  AC Output",
            f"{ac:.1f} W",
            "[white]Inverter output[/white]"
        )
        table.add_row(
            "🔋  Battery Power",
            f"{abs(batt):.1f} W",
            "[green]Charging[/green]"
            if batt > 0
            else "[red]Discharging[/red]"
            if batt < 0
            else "[white]Idle[/white]"
        )
        table.add_row(
            "🔌  Grid Power",
            f"{abs(grid):.1f} W",
            "[red]Importing[/red]"
            if grid > 0
            else "[green]Exporting[/green]"
            if grid < 0
            else "[white]Balanced[/white]"
        )
        table.add_row(
            "🏠  House Consumption",
            f"{house:.1f} W",
            "[white]Total demand[/white]"
        )
        table.add_section()
        table.add_row(
            "⏱  Read timing gap",
            f"{delta:.1f} ms",
            "[green]OK[/green]"
            if delta < 500
            else "[red]HIGH[/red]"
        )

        # Status panel
        buffered_str = (
            f"[yellow]{buf_count} buffered[/yellow]"
            if buf_count > 0
            else "[green]0 buffered[/green]"
        )

        status_panel = Panel(
            f"Device: [{status_colour}]{status}[/{status_colour}]  |  "
            f"Messages: {msg_count}  |  "
            f"Buffered: {buffered_str}  |  "
            f"Time: {time_str}",
            title="System Status",
            border_style=status_colour
        )

        # Faults panel
        if faults:
            fault_text = "\n".join([
                f"[red]⚠  {f['fault']}[/red] at {f['time']}"
                for f in faults
            ])
            fault_panel = Panel(
                fault_text,
                title="Active Faults",
                border_style="red"
            )
        else:
            fault_panel = Panel(
                "[green]No active faults[/green]",
                title="Active Faults",
                border_style="green"
            )

        return Columns([
            Panel(
                table,
                title="⚡ Energy IoT System POC",
                border_style="blue"
            ),
            Columns([status_panel, fault_panel], equal=True)
        ])

    def run(self):
        """Connect and display live dashboard."""
        self.client.connect(self.broker_host, self.broker_port)
        self.client.loop_start()

        console.print(
            "[cyan]Starting Energy Dashboard...[/cyan]"
        )

        with Live(
            self._build_display(),
            refresh_per_second=2,
            console=console
        ) as live:
            try:
                while True:
                    live.update(self._build_display())
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass

        self.client.loop_stop()
        self.client.disconnect()


if __name__ == "__main__":
    dashboard = EnergyDashboard(
        broker_host=os.getenv("MQTT_HOST", "localhost"),
        broker_port=int(os.getenv("MQTT_PORT", "1883"))
    )
    dashboard.run()
