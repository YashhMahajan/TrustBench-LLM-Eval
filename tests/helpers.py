from trustbench.providers import GenResponse, Provider


class FnProvider(Provider):
    """A test provider. The function turns the prompt into the reply text."""
    name = "fn"

    def __init__(self, fn):
        self.fn, self.prompts = fn, []

    async def generate(self, req):
        self.prompts.append(req.prompt)
        text = self.fn(req.prompt)
        if isinstance(text, Exception):
            raise text
        return GenResponse(text, 10, 5, 1.0, req.model)
