class CloserAgent:
    def should_activate(self, intent: str, confidence: float) -> bool:
        return intent == "ready_to_close" and confidence > 0.8
