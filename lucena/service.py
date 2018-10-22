# -*- coding: utf-8 -*-
import tempfile

import zmq

from lucena.controller import Controller
from lucena.io2.socket import Socket
from lucena.worker import Worker


class Service(Worker):

    class Controller(Controller):

        def __init__(self, *args, **kwargs):
            super(Service.Controller, self).__init__(Service, *args, **kwargs)
            self.service_id = self.identity_for(index=0)

        def start(self):
            return super(Service.Controller, self).start(number_of_slaves=1)

        def send(self, message):
            return super(Service.Controller, self).send(
                message=message,
                client_id=b'$controller',
                slave_id=self.service_id
            )

        def recv(self):
            worker, client, message = self.control_socket.recv_from_worker()
            assert client == b'$controller'
            assert worker == self.service_id
            return message

    # Service implementation.

    def __init__(self, worker_factory, endpoint=None, number_of_workers=1):
        # http://zguide.zeromq.org/page:all#Getting-the-Context-Right
        # You should create and use exactly one context in your process.
        super(Service, self).__init__()
        self.worker_factory = worker_factory
        self.endpoint = endpoint if endpoint is not None \
            else "ipc://{}.ipc".format(tempfile.NamedTemporaryFile().name)
        self.number_of_workers = number_of_workers

        self.socket = None
        self.worker_controller = None

        self.worker_ready_ids = None
        self.total_client_requests = 0

    def _plug(self, control_socket, number_of_workers):
        # Init worker queues
        self.worker_ready_ids = []
        # Init sockets
        self.socket = Socket(self.context, zmq.ROUTER)
        self.socket.bind(self.endpoint)
        self.control_socket = control_socket
        self.control_socket.signal(Socket.SIGNAL_READY)
        self.worker_controller = Worker.Controller(self.worker_factory)
        self.worker_ready_ids = self.worker_controller.start(number_of_slaves=number_of_workers)

    def _unplug(self):
        self.socket.close()
        self.control_socket.close()
        self.worker_controller.stop()

    def _handle_poll(self):
        self.poller.register(
            self.control_socket,
            zmq.POLLIN if not self.stop_signal else 0
        )
        self.poller.register(
            self.socket,
            zmq.POLLIN if self.worker_ready_ids and not self.stop_signal else 0
        )
        return dict(self.poller.poll(.1))

    def _handle_socket(self):
        assert len(self.worker_ready_ids) > 0
        client, request = self.socket.recv_from_client()
        worker_name = self.worker_ready_ids.pop(0)
        self.worker_controller.send(worker_name, client, request)
        self.total_client_requests += 1

    def _handle_worker_controller(self):
        worker_id, client, reply = self.worker_controller.recv()
        self.worker_ready_ids.append(worker_id)
        self.socket.send_to_client(client, reply)

    def controller_loop(self, endpoint, identity=None):

        ##
        # Init worker queues
        self.worker_ready_ids = []
        # Init sockets
        self.socket = Socket(self.context, zmq.ROUTER)
        self.socket.bind(self.endpoint)

        self.worker_controller = Worker.Controller(self.worker_factory)
        self.worker_ready_ids = self.worker_controller.start(
            number_of_slaves=self.number_of_workers
        )
        ##

        self.control_socket = Socket(self.context, zmq.REQ, identity=identity)
        self.control_socket.connect(endpoint)
        self.control_socket.send_to_client(b'$controller', {"$signal": "ready"})

        while not self.stop_signal:
            sockets = self._handle_poll()
            if self.control_socket in sockets:
                self._handle_ctrl_socket()
            if self.socket in sockets:
                self._handle_socket()
            if self.worker_controller.message_queued():
                self._handle_worker_controller()

        self._unplug()

    def pending_workers(self):
        return self.worker_ready_ids is not None and \
               len(self.worker_ready_ids) < self.number_of_workers


def create_service(worker_factory=None, endpoint=None, number_of_workers=1):
    return Service.Controller(
        worker_factory,
        endpoint=endpoint,
        number_of_workers=number_of_workers
    )


def main():
    service = create_service(Worker)
    service.start()
    service.send({'$req': 'eval', '$attr': 'total_client_requests'})
    rep = service.recv()
    service.stop()
    print(rep)


if __name__ == '__main__':
    main()
