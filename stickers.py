# pip install telethon
import os
from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetAllStickersRequest, GetStickerSetRequest
from telethon.tl.types import InputStickerSetID

api_id, api_hash = 123, "123"

with TelegramClient("stickers", api_id, api_hash) as c:
    for s in c(GetAllStickersRequest(hash=0)).sets:
        full = c(GetStickerSetRequest(
            InputStickerSetID(id=s.id, access_hash=s.access_hash), hash=0))
        d = os.path.join("stickers", s.short_name)
        os.makedirs(d, exist_ok=True)
        print(f"{s.title}: {len(full.documents)}")
        for i, doc in enumerate(full.documents):
            c.download_media(doc, file=os.path.join(d, f"{i:03d}"))