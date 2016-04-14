from __future__ import print_function, division
import zmq
import numpy as np

import os
import sys

# from . import ParallelNutmeg as Parallel
import threading
import time

import signal

import uuid


_nutmegCore = None
_original_sigint = None
_address = "tcp://localhost"
_pubport = 43686
_subport = _pubport + 1
_timeout = 2000


def init(address=_address, pub_port=_pubport, sub_port=_subport, timeout=_timeout, force=False):
    _core(address, port, timeout, force)


def _core(address=_address, pub_port=_pubport, sub_port=_subport, timeout=_timeout, force=False):
    global _nutmegCore

    if _nutmegCore is None or force:
        _nutmegCore = Nutmeg(address, pub_port, sub_port, timeout)

    elif address != _address or \
            pub_port != _pubport or \
            sub_port != _subport or \
            timeout != _timeout:
        print("WARNING: Module's Nutmeg core already exists with different settings. For multiple instances, manually instantiate a Nutmeg.Nutmeg(...) object.")

    return _nutmegCore


def initialized():
    return _nutmegCore is not None and _nutmegCore.initialized


def figure(handle, figureDef, sync=True):
    return _core().figure(handle, figureDef, sync)


def ndarray_to_message(array):
    header = dict(type=str(array.dtype), shape=array.shape)
    return header, array.tobytes()


def to_nutmeg_message(value):
    '''
    Recursively convert any numpy.ndarrays into lists in preparation for
    JSONification.
    '''
    binary = []
    binary_data = []

    new_value = _to_nut(value, binary, binary_data)
    return new_value, binary, binary_data


def _to_nut(value, binary, binary_data):
    ''' Helper method for to_nutmeg_message '''
    if isinstance(value, np.ndarray):
        # Check if the array needs binarizing
        if value.dtype == 'O':  # Check not object type
            return _to_nut( value.tolist(), binary, binary_data )
        else:
            header, data = ndarray_to_message(value)
            label = "$bin{:d}$".format(len(binary))
            binary.append(header)
            binary_data.append(data)
            return label

    elif isinstance(value, list):
        new_value = [_to_nut(sub_value, binary, binary_data) for sub_value in value]
        return new_value

    elif isinstance(value, dict):
        new_value = { key: _to_nut(sub_value, binary, binary_data) for key, sub_value in value.items() }
        return new_value

    else:
        return value


class QMLException(Exception):
    pass


class NutmegException(Exception):
    pass


# Threaded decorator
def _threaded(fn):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=fn, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
    return wrapper


class Nutmeg:

    def __init__(self, address=_address, pub_port=_pubport, sub_port=_subport, timeout=_timeout, pingperiod=10000):
        '''
        :param timeout: Timeout in ms
        '''
        self.initialized = False
        self.host = address
        self.pub_port = pub_port
        self.sub_port = sub_port
        self.pub_address = address + ":" + str(pub_port)
        self.sub_address = address + ":" + str(sub_port)
        self.timeout = timeout

        self.task_count = 0
        self.session = uuid.uuid1()

        # Create the socket and connect to it.
        self.running = True
        self.context = zmq.Context()
        self.pubsock = None
        self.poller = None
        self.reset_socket()

        self.socket_lock = threading.Lock()

        self.figures = {}

    def reset_socket(self):
        # Close things if necessary
        if self.pubsock is not None:
            # if self.poller is not None:
            #     self.poller.unregister(self.pubsock)
            self.pubsock.close()

        self.pubsock = self.context.socket(zmq.PUB)
        # self.socket.setsockopt(zmq.LINGER, 0)
        print("Publishing to:", self.pub_address)
        self.pubsock.connect(self.pub_address)

        # self.poller = zmq.Poller()
        # self.poller.register(self.socket, zmq.POLLIN)

        # # Last time a disconnection occurred
        # self.disconnected_t = time.time()
        self.running = True

    def ready(self):
        return self.running

    def _publish(self, msg, binary_data):
        # Check socketlock
        self.socket_lock.acquire()
        try:
            # Inject task ID (thread safe in here)
            msg['id'] = self.task_count
            self.task_count += 1

            # Send message
            # print("Sending:", "Nutmeg")
            self.pubsock.send(b"Nutmeg", flags=zmq.SNDMORE)
            # print("Sending:", msg)
            self.pubsock.send_json(msg, flags=zmq.SNDMORE)
            # Then data
            for data in binary_data:
                # print("Sending binary")
                self.pubsock.send(data, flags=zmq.SNDMORE, copy=True)

            # Makes code nicer just simply having a "null message"
            self.pubsock.send(b'')

        except IOError:
            raise

        finally:
            self.socket_lock.release()

    # @_threaded
    def publish_message(self, msg):
        '''
        Process the message for numpy arrays and convert them to Nutmeg-ready
        message data before sending
        '''
        msg, binary_header, binary_data = to_nutmeg_message(msg)
        msg['binary'] = binary_header
        msg['session'] = str(self.session)

        self._publish(msg, binary_data)

    def figure(self, handle, figureDef, sync=True):
        # We're going by the interesting assumption that a file path cannot be
        # used to define a QML layout...
        qml = ""

        if figureDef.endswith('.qml') or os.path.exists(figureDef):
            if os.path.exists(figureDef):
                with open(figureDef, 'r') as F:
                    qml = F.read()  #.encode('UTF-8')
            else:
                raise(QMLException("File, %s, does not exist." % figureDef))
        else:
            qml = figureDef

        msg = dict(command="SetFigure", target=handle, args=[qml])
        self.publish_message(msg)

        return Figure(self, handle, address=self.host, pub_port=self.pub_port, qml=qml)

    def set_gui(self, handle, qml):
        msg = dict(command="SetGui", target=handle, args=[qml])
        self.publish_message(msg)

    def set_property(self, handle, value):
        '''
        Set property at handle
        '''
        msg = dict(command="SetProperty", target=handle, args=[value])
        self.publish_message(msg)

    def set_properties(self, handle, **properties):
        for name, value in properties.items():
            self.set_property('.'.join((handle, name)), value)

    def set_parameter(self, handle, value):
        '''
        Set property at handle
        '''
        msg = dict(command="SetParam", target=handle, args=[value])
        self.publish_message(msg)

    def set_parameters(self, handle, **params):
        for name, value in params.items():
            self.set_parameter('.'.join(handle, name), value)


class NutmegObject(object):
    def __init__(self, handle):
        self.handle = handle

    # def __getattr__(self, name):
    #     newHandle = self.handle + '.' + name
    #     if isValidHandle(newHandle):
    #         return NutmegObject(newHandle)
    #     else:
    #         raise(NutmegException("%s has no object with handle, %s" % (self.handle, name)))


class Figure(NutmegObject):
    def __init__(self, nutmeg, handle, address, pub_port, qml):
        self.nutmeg = nutmeg
        self.handle = handle
        self.qml = qml
        self.parameters = {}
        self.updates = {}
        self.address = address
        # self.updateAddress = address + ":" + str(port)
        # self._setlock = threading.Lock()

        self.sent = False
        # self.send()

    @_threaded
    def _waitForUpdates(self):
        self.updateSocket = self.nutmeg.context.socket(zmq.REQ)
        print("Connecting to socket at: %s" % self.updateAddress)
        self.updateSocket.connect(self.updateAddress)
        print("Connected")

        while True:
            # print("Send socket ready")
            self.updateSocket.send(b"ready")

            msg = self.updateSocket.recv_json()
            command = msg[0]
            # print("Received update %s" % msg)

            if command == 'updateParam':
                figureHandle, parameter, value = msg[1:4]
                self.updateParameter(parameter, value)

            elif command == 'ping':
                time.sleep(0.01)

            elif command == 'exception':
                print("TODO: Handle exceptions here...")

    def setGui(self, guiDef):
        # We're going by the interesting assumption that a file path cannot be
        # used to define a QML layout...
        qml = ""
        if os.path.exists(guiDef):
            with open(guiDef, 'r') as F:
                qml = F.read()  #.encode('UTF-8')
        else:
            qml = guiDef

        self.nutmeg.set_gui(qml)

    def updateParameter(self, param, value):
        # print("Update Parameter:", param, value)
        self.parameters[param].updateValue(value)

        # Call updates if there are any attached to this parameter
        if param not in self.updates:
            return
        updatesToCall = self.updates[param]

        # We use a set so updates aren't initialised multiple times
        for update in updatesToCall:
            update.parametersChanged(self.parameters, param)

    def parameter(self, param):
        return self.parameters[param]

    def set(self, handle, *value, **properties):
        '''
        Set the properties in `handle` according to the provided dictionary of
        properties and their values.

        For example: figure.set('ax[1].data', {'x': range(10), 'y': someData})

        :param handle: A string to the object or property of interest
        :param properties: A dictionary mapping properties to values or just the property values themselves. If `properties` is not a dictionary, Nutmeg will assume that `handle` points directly to a property.
        '''
        full_handle = self.handle + "." + handle

        if len(value) > 1:
            print("WARNING: Values after first value ignored")
        if len(value) > 0:
            if len(properties) > 0:
                print("WARNING: Keyword arguments ignored")

            self.nutmeg.set_property(full_handle, value[0])

        else:
            self.nutmeg.set_properties(full_handle, **properties)

    def set_parameters(self, **params):
        self.nutmeg.set_parameters(self.handle, **params)

    def set_parameter_properties(self, param, **properties):
        handle = self.handle + '.' + param
        self.nutmeg.set_parameters(handle, **properties)

    # def register_update(self, update, handle=""):
    #     # The update needs to know the figure and handle of the targets
    #     update.handle = handle
    #     update.figure = self
    #     # Add update to the list for each registered parameter
    #     requiredParamsLoaded = True
    #     for param in update.params:
    #         if param not in self.updates:
    #             self.updates[param] = []
    #         self.updates[param].append(update)
    #         # Check if this parameter is loaded
    #         if requiredParamsLoaded and param not in self.parameters:
    #             requiredParamsLoaded = False

    #     if requiredParamsLoaded:
    #         update.initializeParameters(self.parameters)


class Parameter():
    '''
    Keep track of a parameter's value and state. Currently, the parameter
    cannot be modified from Python.
    '''
    def __init__(self, name, value=0, changed=0, figure=None):
        self.name = name
        self.value = value
        self._changed = changed
        self.callbacks = []
        self.valueLock = threading.Lock()
        self.changedLock = threading.Lock()
        self.figure = figure

    @property
    def changed(self):
        return self._changed

    @changed.setter
    def changed(self, value):
        self.changedLock.acquire()
        self._changed = value
        if value == True:
            for callback in self.callbacks:
                callback()
        self.changedLock.release()

    def updateValue(self, value):
        self.valueLock.acquire()
        self.value = value
        self.changed += 1
        self.valueLock.release()

    def read(self):
        '''
        Return the value of the parameter and set the changed to False
        '''
        self.changed = 0
        return self.value

    def set(self, *value, **properties):
        if self.figure is None:
            print("WARNING: Figure of parameter is None")
            return

        if len(value) == 0 and len(properties) == 0:
            return

        if len(value) > 0 and len(properties) > 0:
            print("WARNING: Keyword properties of parameter.set() override ordered args.")

        if len(properties) > 0:
            self.figure.set_parameter_properties(self.name, **properties)

        elif len(value) > 0:
            kwargs = { self.name: value[0] }
            self.figure.set_parameters(**kwargs)

    def registerCallback(self, callback):
        '''
        Call this function whenever the value is changed.
        '''
        self.callbacks.append(callback)


def exit_gracefully(signum, frame):
    # stackoverflow.com/a/18115530/1512137
    # restore the original signal handler as otherwise evil things will happen
    # in raw_input when CTRL+C is pressed, and our signal handler is not re-entrant
    signal.signal(signal.SIGINT, _original_sigint)

    try:
        if raw_input("\nReally quit? (y/n)> ").lower().startswith('y'):
            sys.exit(1)

    except KeyboardInterrupt:
        print("Ok ok, quitting")
        sys.exit(1)

    # restore the exit gracefully handler here    
    signal.signal(signal.SIGINT, exit_gracefully)
