
import asyncio
import json
import os
from typing import Optional

from dotenv import load_dotenv
from deepgram import AsyncDeepgramClient

load_dotenv()

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

if not DEEPGRAM_API_KEY:
    raise ValueError("Missing DEEPGRAM_API_KEY in .env")


current_draft_task: Optional[asyncio.Task] = None


async def prepare_draft_response(transcript: str):
    """Mock speculative response started on EagerEndOfTurn."""
    print(f"\n[AGENT] Preparing speculative response for: {transcript}")

    await asyncio.sleep(2)

    print(f"[AGENT] Draft response ready: You said -> {transcript}")


async def finalize_response(transcript: str):
    """Mock final response started on EndOfTurn."""
    print(f"\n[AGENT] Finalizing response for: {transcript}")

    await asyncio.sleep(1)

    print(f"[AGENT] FINAL RESPONSE: Got it, you said: {transcript}\n")


async def cancel_draft_response():
    global current_draft_task

    if current_draft_task and not current_draft_task.done():
        print("\n[AGENT] User continued speaking, cancelling draft response...")

        current_draft_task.cancel()

        try:
            await current_draft_task
        except asyncio.CancelledError:
            print("[AGENT] Draft response cancelled")


async def keep_alive(connection):
    """Deepgram closes idle sockets after ~10s without audio."""
    while True:
        await asyncio.sleep(3)
        try:
            await connection.send(json.dumps({"type": "KeepAlive"}))
        except Exception:
            break


async def main():
    global current_draft_task

    deepgram = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY)

    # Latest Flux SDK API
    async with deepgram.listen.v2.connect(
        model="flux-general-en",
        encoding="linear16",
        sample_rate=16000,
        eot_threshold=0.7,
        eager_eot_threshold=0.45,
        eot_timeout_ms=3000,
    ) as connection:

        print("Connected to Deepgram Flux")

            # Start keepalive background task
        asyncio.create_task(keep_alive(connection))

        print("Speak into your microphone stream...")

        while True:
            message = await connection.recv()

            if isinstance(message, bytes):
                continue

            if not isinstance(message, dict):
                try:
                    message = json.loads(message)
                except Exception:
                    print("Unknown message:", message)
                    continue

        msg_type = message.get("type")

        # Regular transcript updates while user is speaking
        if msg_type == "Update":
            transcript = message.get("transcript", "")
            if transcript:
                print(f"[UPDATE] {transcript}")

        # Flux thinks the user may be done; begin speculative reply
        elif msg_type == "EagerEndOfTurn":
            transcript = message.get("transcript", "")
            print(f"\n[EAGER END OF TURN] {transcript}")

            await cancel_draft_response()

            current_draft_task = asyncio.create_task(
                prepare_draft_response(transcript)
            )

        # User resumed speaking after eager end of turn
        elif msg_type == "TurnResumed":
            print("\n[TURN RESUMED] User kept talking")
            await cancel_draft_response()

        # Final confirmed end of turn
        elif msg_type == "EndOfTurn":
            transcript = message.get("transcript", "")
            print(f"\n[END OF TURN] {transcript}")

            if current_draft_task and not current_draft_task.done():
                try:
                    await current_draft_task
                except asyncio.CancelledError:
                    pass

            await finalize_response(transcript)

        elif msg_type == "Error":
            print("[ERROR]", message)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by user")

