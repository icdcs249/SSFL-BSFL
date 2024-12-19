import time
import json
import torch
import random
import requests
from torch import nn
from pathlib import Path
from torchvision import datasets
from torch.utils.data import DataLoader
from torchvision.transforms import ToTensor



class Client:
    def __init__(self, port, ClientNN, malicious=False):
        self.port = port
        self.batch_size = 128
        self.epochs = 5
        self.num_nodes = 9
        # self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = "cpu"
        self.ClientNN = ClientNN
        self.model = self.ClientNN().to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=3e-4)
        self.get_data()
        self.malicious = malicious
        self.root_path = Path(__file__).resolve().parents[1]

    def get_data(self):
        training_dataset = datasets.FashionMNIST(
            root="data",
            train=False,
            download=False,
            transform=ToTensor()
        )
        test_dataset = datasets.FashionMNIST(
            root="data",
            train=False,
            download=False,
            transform=ToTensor()
        )
        data_portion = len(training_dataset) // 9
        indexes = [random.randint(0, len(training_dataset) - 1) for _ in range(data_portion)]

        test_portion = len(test_dataset) // 9
        test_indexes = [random.randint(0, len(test_dataset) - 1) for _ in range(test_portion)]

        self.training_dataset = torch.utils.data.Subset(training_dataset, indexes)
        self.test_dataset = torch.utils.data.Subset(test_dataset, test_indexes)

        self.training_dataloader = DataLoader(
            self.training_dataset, batch_size=self.batch_size)
        self.test_dataloader = DataLoader(
            self.test_dataset, batch_size=test_portion)

    def train(self, server_port):
        if self.malicious:
            self.attack(server_port)
        else:
            self.model.train()
            losses = []
            for _ in range(self.epochs):
                epoch_loss = 0
                for batch, (X, y) in enumerate(self.training_dataloader):
                    X = X.to(self.device)
                    self.model = self.model.to(self.device)
                    output = self.model(X)
                    clientOutput = output.clone().detach().requires_grad_(True)
                    res = requests.post(f"http://localhost:{server_port}/server/train/",
                                        json={
                                            "client_port" : self.port,
                                            "batch": batch,
                                            "clientOutput": json.dumps(clientOutput.tolist()),
                                            "targets": json.dumps(y.tolist())
                                        })
                    status = json.loads(res.content.decode())["status"]
                    while status == "In progress":
                        time.sleep(0.1)
                        res = requests.post(f"http://localhost:{server_port}/server/tasks/",
                                            json={
                                                "client_port" : self.port
                                            })
                        status = json.loads(res.content.decode())["status"]
                    grads = torch.tensor(json.loads(json.loads(res.content.decode())["grads"])).to(self.device)
                    epoch_loss += json.loads(res.content.decode())["loss"]
                    output.backward(grads)
                    self.optimizer.step()
                    self.optimizer.zero_grad()
                losses.append(epoch_loss/len(self.training_dataloader))
            torch.save(self.model.state_dict(), f"{self.root_path}/split_learning/models/node_{self.port-8000}_client.pth")
            requests.post(f"http://localhost:{server_port}/server/round/",
                                            json={
                                                "client_port" : self.port,
                                                "losses" : losses
                                            })
    
    def attack(self, server_port):
        losses = []
        for _ in range(self.epochs):
            epoch_loss = 0
            for batch, (X, y) in enumerate(self.training_dataloader):
                X = torch.randn_like(X).to(self.device)
                self.model = self.model.to(self.device)
                output = self.model(X)
                clientOutput = output.clone().detach().requires_grad_(True)
                res = requests.post(f"http://localhost:{server_port}/server/train/",
                                    json={
                                        "client_port" : self.port,
                                        "batch": batch,
                                        "clientOutput": json.dumps(clientOutput.tolist()),
                                        "targets": json.dumps(y.tolist())
                                    })
                status = json.loads(res.content.decode())["status"]
                while status == "In progress":
                    time.sleep(0.1)
                    res = requests.post(f"http://localhost:{server_port}/server/tasks/",
                                        json={
                                            "client_port" : self.port
                                        })
                    status = json.loads(res.content.decode())["status"]
                epoch_loss += json.loads(res.content.decode())["loss"]
            losses.append(epoch_loss/len(self.training_dataloader))
        torch.save(self.model.state_dict(), f"{self.root_path}/split_learning/models/node_{self.port-8000}_client.pth")
        requests.post(f"http://localhost:{server_port}/server/round/",
                                        json={
                                            "client_port" : self.port,
                                            "losses" : losses
                                        })
        
    def predict(self, path):
        model = self.ClientNN().to("cpu")
        model.load_state_dict(torch.load(path))
        model.eval()
        with torch.no_grad():
            for X, y in self.test_dataloader:
                X = X.to("cpu")
                output = model(X)
                return output.clone().detach(), y