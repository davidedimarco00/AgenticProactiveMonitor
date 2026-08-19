import asyncio
import spade

from spade.agent import Agent
from spade.behaviour import OneShotBehaviour
from spade.message import Message


class SenderAgent(Agent):

    class SendBehaviour(OneShotBehaviour):

        async def run(self):
            # Piccola attesa per essere sicuri che la sessione sia pronta.
            await asyncio.sleep(1)

            msg = Message(to="receiver@xmpp")
            msg.body = "Ciao! Questo messaggio è passato tramite XMPP/Prosody."

            print("[SENDER] Invio messaggio...")
            print(f"[SENDER] Destinatario: {msg.to}")

            await self.send(msg)

            print("[SENDER] Messaggio inviato correttamente.")

            await asyncio.sleep(1)
            await self.agent.stop()

    async def setup(self):
        print(f"[SENDER] Agent avviato: {self.jid}")
        self.add_behaviour(self.SendBehaviour())


async def main():
    sender = SenderAgent(
        "sender@xmpp",
        "sender-password",
        port=5222,
        verify_security=False,
    )

    print("[SENDER] Connessione a Prosody...")

    await sender.start(auto_register=True)

    print("[SENDER] Connesso e autenticato.")

    await spade.wait_until_finished(sender)


if __name__ == "__main__":
    spade.run(main())