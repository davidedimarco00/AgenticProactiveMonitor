import spade
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour


class ReceiverAgent(Agent):

    class ReceiveBehaviour(CyclicBehaviour):

        async def run(self):
            print("[RECEIVER] In attesa di un messaggio...")

            msg = await self.receive(timeout=30)

            if msg:
                print("\n==============================")
                print("MESSAGGIO RICEVUTO")
                print("==============================")
                print(f"Da:   {msg.sender}")
                print(f"A:    {msg.to}")
                print(f"Body: {msg.body}")
                print("==============================\n")

                await self.agent.stop()

            else:
                print("[RECEIVER] Timeout: nessun messaggio ricevuto.")
                await self.agent.stop()

    async def setup(self):
        print(f"[RECEIVER] Agent avviato: {self.jid}")
        self.add_behaviour(self.ReceiveBehaviour())


async def main():
    receiver = ReceiverAgent(
        "receiver@xmpp",
        "receiver-password",
        port=5222,
        verify_security=False,
    )

    print("[RECEIVER] Connessione a Prosody...")

    await receiver.start(auto_register=True)

    print("[RECEIVER] Connesso e autenticato.")

    await spade.wait_until_finished(receiver)


if __name__ == "__main__":
    spade.run(main())