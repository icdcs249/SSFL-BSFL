import os
import re
import copy
import json
import torch
import requests
import threading
import numpy as np
from torch import nn


class Server:
    def __init__(self, port, ServerNN):
        self.port = port
        self.rounds = 3
        self.current_round = 0
        self.current_cycle = 0
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.lr = 3e-4
        self.ServerNN = ServerNN
        self.avg_model = self.ServerNN().to(self.device)
        self.models = {}
        self.losses = {}
        self.round_completion = {}
        self.semaphore = threading.Semaphore(1)

    def load_model(self):
        self.avg_model.load_state_dict(torch.load("./models/global_server.pth"))

    def get_model(self, client_port):
        with self.semaphore:
            return self.models[client_port]

    def set_model(self, model, client_port):
        with self.semaphore:
            self.models[client_port].load_state_dict(model.state_dict())

    def train(self, client_port, batch, clientOutputCPU, targets):
        model = self.get_model(client_port)
        optimizer = torch.optim.Adam(model.parameters(), lr=self.lr)
        loss_fn = nn.CrossEntropyLoss()
        model = model.to(self.device)
        targets = targets.to(self.device)
        optimizer.zero_grad()
        clientOutputCPU = torch.tensor(clientOutputCPU).requires_grad_(True)
        clientOutput = clientOutputCPU.to(self.device)
        pred = model(clientOutput)
        loss = loss_fn(pred, targets)
        loss.backward()
        optimizer.step()
        grad = clientOutputCPU.grad.clone().detach()
        model = model.to("cpu")
        torch.cuda.empty_cache()
        self.set_model(model, client_port)
        return grad, loss.item()

    def predict(self, clientOutputCPU, path):
        torch.cuda.empty_cache()
        model = self.ServerNN().to("cpu")
        model.load_state_dict(torch.load(path))
        model.eval()
        with torch.no_grad():
            clientOutputCPU = clientOutputCPU.clone().detach().requires_grad_(False)
            clientOutput = clientOutputCPU.to("cpu")
            return model(clientOutput)

    def aggregate(self):
        clients = list(self.models.keys())
        weights_avg = copy.deepcopy(self.models[clients[0]].state_dict())
        for k in weights_avg.keys():
            for i in range(1, len(clients)):
                weights_avg[k] += self.models[clients[i]].state_dict()[k]
            weights_avg[k] = torch.div(weights_avg[k], len(clients))
        self.avg_model.load_state_dict(weights_avg)

    def finish_round(self, client, losses):
        self.losses[self.current_cycle][client][self.current_round] = losses
        self.round_completion[client] = True
        for client, completed in self.round_completion.items():
            if not completed:
                return
        self.aggregate()
        if self.current_round != self.rounds:
            self.start_round()
        else:
            self.finish_training()

    def get_model_paths(self):
        cwd = os.path.dirname(__file__)
        model_name = f"node_{self.port-8000}_server"
        server_model_path = os.path.abspath(
            os.path.join(cwd, f"/Users/amirrezasokhankhosh/Documents/Workstation/splitfed/multi_split_fed/models/{model_name}.pth"))
        client_models_path = []
        for client in self.clients:
            model_name = f"node_{client-8000}_client"
            client_models_path.append(os.path.abspath(
                os.path.join(cwd, f"/Users/amirrezasokhankhosh/Documents/Workstation/splitfed/multi_split_fed/models/{model_name}.pth")))
        return server_model_path, client_models_path

    def finish_training(self):
        server_model_path, client_models_path = self.get_model_paths()

        torch.save(self.avg_model.state_dict(), server_model_path)

        requests.post(f"http://localhost:5050/server/cycle/",
                                            json={
                                                "server_port" : self.port
                                            })

        self.save_losses()
        print("Training Completed.")

    def start(self, clients, current_cycle):
        self.current_cycle = current_cycle
        self.losses[self.current_cycle] = {}
        self.clients = clients
        for client in self.clients:
            self.losses[self.current_cycle][client] = {}
            requests.get(f"http://localhost:{client}/client/load/")
        self.load_model()
        self.current_round = 0
        self.start_round()

    def start_round(self):
        self.current_round += 1
        self.models = {}
        for client in self.clients:
            self.models[client] = copy.deepcopy(self.avg_model)
            self.losses[self.current_cycle][client][self.current_round] = []
            self.round_completion[client] = False

        for client in self.clients:
            requests.get(f"http://localhost:{client}/client/train/",
                         json={
                             "server_port": self.port
                         })

    def save_losses(self):
        file = open(f"/Users/amirrezasokhankhosh/Documents/Workstation/splitfed/multi_split_fed/losses/node_{self.port-8000}.json", "w")
        file.write(json.dumps(self.losses))