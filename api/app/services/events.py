import asyncio


# tiny publish/subscribe. the dashboard listens to it through server sent events (routes/events.py)
class EventBus:
    def __init__(self):
        self.listeners = set()

    def subscribe(self):
        queue = asyncio.Queue(maxsize=100)
        self.listeners.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.listeners.discard(queue)

    def publish(self, type, data=None):
        event = {"type": type, "data": data}
        for queue in self.listeners:
            # a listener that is too slow just misses events, it never blocks us
            if not queue.full():
                queue.put_nowait(event)
