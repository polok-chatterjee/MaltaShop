"""Renders the sequence diagrams (docs/*.png). Run: python docs/make_diagrams.py"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

plt.rcParams["font.family"] = "DejaVu Sans"


class Seq:
    def __init__(self, names, xs, height, width=9.2, title=None):
        self.fig, self.ax = plt.subplots(figsize=(width, height * 0.52), dpi=170)
        self.ax.set_xlim(0, width); self.ax.set_ylim(height, -1.4); self.ax.axis("off")
        self.x = dict(zip(names, xs)); self.height = height
        if title:
            self.ax.text(width / 2, -1.15, title, ha="center", fontsize=11, fontweight="bold")
        for n, x in self.x.items():
            self.ax.add_patch(FancyBboxPatch((x - 0.64, -0.55), 1.28, 0.62, boxstyle="round,pad=0.02",
                                             fc="#E8EEF6", ec="#1F3A5F", lw=1.2))
            self.ax.text(x, -0.24, n, ha="center", va="center", fontsize=6.6, fontweight="bold", linespacing=1.1)
            self.ax.plot([x, x], [0.1, height], color="#9AA7B5", lw=0.9, ls=(0, (4, 3)), zorder=0)

    def band(self, y1, y2, label, color):
        self.ax.add_patch(Rectangle((0.05, y1), 9.1, y2 - y1, fc=color, ec="none", alpha=0.35, zorder=-1))
        self.ax.text(0.12, y1 + 0.25, label, fontsize=8, fontweight="bold", color="#222")

    def msg(self, a, b, y, text, dashed=False, color="#222222", above=True, fs=6.9):
        xa, xb = self.x[a], self.x[b]
        if a == b:                                    # internal step: small box beside the lifeline
            w = 0.075 * max(len(t) for t in text.split("\n")) + 0.3
            cx = xa + 0.15 + w / 2 if xa < 6 else xa - 0.15 - w / 2
            self.ax.add_patch(Rectangle((cx - w / 2, y - 0.27), w, 0.54 * (1 + text.count("\n") * 0.6),
                                        fc="#F3F5F8", ec="#8A97A6", lw=0.8))
            self.ax.text(cx, y - 0.27 + 0.27 * (1 + text.count("\n") * 0.6), text, ha="center", va="center",
                         fontsize=fs, color=color, linespacing=1.15)
            self.ax.plot([xa, xa + (0.15 if xa < 6 else -0.15)], [y, y], color="#8A97A6", lw=0.8)
            return
        self.ax.annotate("", xy=(xb, y), xytext=(xa, y),
                         arrowprops=dict(arrowstyle="-|>", color=color, lw=1.1, ls="--" if dashed else "-"))
        self.ax.text((xa + xb) / 2, y - 0.17, text, fontsize=fs, ha="center", va="bottom", color=color)

    def note(self, x, y, text, w=2.4, h=0.55, fc="#FFF4DC"):
        self.ax.add_patch(Rectangle((x - w / 2, y - h / 2), w, h, fc=fc, ec="#B79A4B", lw=0.8))
        self.ax.text(x, y, text, ha="center", va="center", fontsize=6.6, linespacing=1.15)

    def save(self, path):
        self.fig.savefig(path, bbox_inches="tight", facecolor="white"); plt.close(self.fig)


# ============================ 1. event flow ============================
names = ["Client", "Order Service\n(API)", "Order DB", "Outbox relay\n(thread)", "RabbitMQ", "Notification\nService", "Notification\nDB"]
s = Seq(names, [0.8, 2.15, 3.6, 4.95, 6.3, 7.5, 8.7], 20.2, title="OrderCreated event flow (asynchronous)")
s.msg("Client", "Order Service\n(API)", 1.4, "1. POST /orders  (X-Correlation-ID)")
s.msg("Order Service\n(API)", "Order Service\n(API)", 2.3, "2. validate request")
s.msg("Order Service\n(API)", "Order DB", 3.8, "3. ONE transaction: order + saga +\noutbox rows (OrderCreated, ReserveInventory)")
s.msg("Order DB", "Order Service\n(API)", 4.5, "committed", dashed=True)
s.msg("Order Service\n(API)", "Client", 6.1, "4. 201 Created (saga_state=STARTED)", color="#0B6B2E")
s.note(2.9, 6.9, "Response is sent NOW: no waiting for the broker or any consumer", w=4.6, h=0.5, fc="#E6F4EA")
s.msg("Order Service\n(API)", "Outbox relay\n(thread)", 8.0, "5. wake (non-blocking)", dashed=True)
s.msg("Outbox relay\n(thread)", "Order DB", 8.9, "read unpublished outbox rows", dashed=True)
s.msg("Outbox relay\n(thread)", "RabbitMQ", 10.1, "6. publish order.created\n(persistent, correlation id)")
s.msg("RabbitMQ", "Outbox relay\n(thread)", 10.9, "7. publisher confirm", dashed=True)
s.msg("Outbox relay\n(thread)", "Order DB", 11.8, "8. mark outbox row published", dashed=True)
s.msg("RabbitMQ", "Notification\nService", 13.2, "9. deliver (notification.order_events)")
s.msg("Notification\nService", "Notification\nDB", 14.5, "10. ONE transaction: event_id -> processed_events\n+ notification row (duplicate => skipped)")
s.msg("Notification\nService", "Notification\nService", 15.5, "11. deliver (simulated): SENT")
s.msg("Notification\nService", "Notification\nService", 16.5, "12. log: event processed\nsuccessfully (same correlation_id)")
s.msg("Notification\nService", "RabbitMQ", 18.0, "13. basic_ack (message removed)", color="#0B6B2E")
s.note(4.5, 19.3, "Failure: transient error => retry with backoff, then NACK => dead-letter queue.\nConsumer down => message simply waits in the durable queue.", w=7.4, h=0.75, fc="#FDECEA")
s.save("docs/sequence_event_flow.png")

# ============================ 2. saga ============================
names = ["Client", "Order Service\n(orchestrator)", "Inventory\n(simulated)", "Payment\n(simulated)", "Notification"]
s = Seq(names, [0.95, 2.9, 5.0, 6.9, 8.5], 40.5, title="Order saga: orchestration and compensation (messages travel via RabbitMQ)")
y = 1.4
s.band(0.9, 9.6, "A. Happy path", "#E6F4EA")
s.msg("Client", "Order Service\n(orchestrator)", y + 0.5, "POST /orders  ->  201 (order PENDING)")
s.msg("Order Service\n(orchestrator)", "Notification", y + 1.5, "OrderCreated (event)")
s.msg("Order Service\n(orchestrator)", "Inventory\n(simulated)", y + 2.6, "ReserveInventory (command)")
s.msg("Inventory\n(simulated)", "Order Service\n(orchestrator)", y + 3.6, "InventoryReserved (reply)", dashed=True)
s.msg("Order Service\n(orchestrator)", "Payment\n(simulated)", y + 4.7, "ProcessPayment (command)")
s.msg("Payment\n(simulated)", "Order Service\n(orchestrator)", y + 5.7, "PaymentCompleted (reply)", dashed=True)
s.msg("Order Service\n(orchestrator)", "Order Service\n(orchestrator)", y + 6.5, "order CONFIRMED, saga COMPLETED")
s.msg("Order Service\n(orchestrator)", "Notification", y + 7.6, "OrderConfirmed (event)")

y0 = 10.4
s.band(y0, y0 + 6.2, "B. Compensation 1: inventory reservation fails", "#FDECEA")
s.msg("Order Service\n(orchestrator)", "Inventory\n(simulated)", y0 + 1.5, "ReserveInventory (command)")
s.msg("Inventory\n(simulated)", "Order Service\n(orchestrator)", y0 + 2.6, "InventoryReservationFailed\n(insufficient stock)", dashed=True)
s.msg("Order Service\n(orchestrator)", "Order Service\n(orchestrator)", y0 + 3.5, "order CANCELLED (nothing to undo)")
s.msg("Order Service\n(orchestrator)", "Notification", y0 + 4.9, "OrderCancelled (event)")
s.note(6.9, y0 + 3.2, "Payment is never attempted", w=2.3, h=0.5)

y1 = 17.1
s.band(y1, y1 + 10.0, "C. Compensation 2: payment fails -> release inventory", "#FDECEA")
s.msg("Order Service\n(orchestrator)", "Inventory\n(simulated)", y1 + 1.5, "ReserveInventory  ->  InventoryReserved (ok)")
s.msg("Order Service\n(orchestrator)", "Payment\n(simulated)", y1 + 2.8, "ProcessPayment (command)")
s.msg("Payment\n(simulated)", "Order Service\n(orchestrator)", y1 + 3.8, "PaymentFailed (declined)", dashed=True)
s.msg("Order Service\n(orchestrator)", "Order Service\n(orchestrator)", y1 + 4.6, "order CANCELLED, saga COMPENSATING")
s.msg("Order Service\n(orchestrator)", "Inventory\n(simulated)", y1 + 6.0, "ReleaseInventory (COMPENSATING command)", color="#B3261E")
s.msg("Inventory\n(simulated)", "Order Service\n(orchestrator)", y1 + 7.0, "InventoryReleased (reply)", dashed=True)
s.msg("Order Service\n(orchestrator)", "Order Service\n(orchestrator)", y1 + 7.8, "saga CANCELLED (compensation complete)")
s.msg("Order Service\n(orchestrator)", "Notification", y1 + 9.2, "OrderCancelled (event)")

y2 = 27.7
s.band(y2, y2 + 11.6, "D. Compensation 3: order cancelled AFTER successful payment -> refund", "#FDECEA")
s.msg("Client", "Order Service\n(orchestrator)", y2 + 1.4, "POST /orders/{id}/cancel  (order is CONFIRMED)")
s.msg("Order Service\n(orchestrator)", "Client", y2 + 2.3, "202 Accepted", dashed=True)
s.msg("Order Service\n(orchestrator)", "Order Service\n(orchestrator)", y2 + 3.1, "order CANCELLED, saga COMPENSATING")
s.msg("Order Service\n(orchestrator)", "Payment\n(simulated)", y2 + 4.6, "RefundPayment (COMPENSATING command)", color="#B3261E")
s.msg("Order Service\n(orchestrator)", "Inventory\n(simulated)", y2 + 5.7, "ReleaseInventory (COMPENSATING command)", color="#B3261E")
s.msg("Payment\n(simulated)", "Order Service\n(orchestrator)", y2 + 6.8, "PaymentRefunded (reply)", dashed=True)
s.msg("Inventory\n(simulated)", "Order Service\n(orchestrator)", y2 + 7.8, "InventoryReleased (reply)", dashed=True)
s.msg("Order Service\n(orchestrator)", "Order Service\n(orchestrator)", y2 + 8.6, "both confirmed: saga CANCELLED")
s.msg("Order Service\n(orchestrator)", "Notification", y2 + 10.2, "OrderCancelled (event)")
s.save("docs/sequence_saga.png")
print("diagrams written")
