from dataclasses import dataclass


@dataclass(slots=True)
class ListingDocument:
    listing_id: str
    text: str
    language: str
    metadata: dict


def chunk_listing(doc: ListingDocument) -> list[ListingDocument]:
    """Property listings are short; keep one chunk per listing."""
    return [doc]
