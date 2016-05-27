from __future__ import print_function, division
import zmq
import numpy as np

import os
import sys

# from . import ParallelNutmeg as Parallel
import threading
import time

import queue
from collections import OrderedDict

import signal

import uuid


_nutmegCore = None
_original_sigint = None
_address = "tcp://localhost"
_pubport = 43686
_subport = _pubport + 1
_timeout = 2000
_sync = False

# TODO: Handle ipc://...


def init(address=_address, pub_port=_pubport, sub_port=_subport, timeout=_timeout, sync=_sync, force=False):
    _core(address, pub_port, sub_port, timeout, sync, force)


def _core(address=_address, pub_port=_pubport, sub_port=_subport, timeout=_timeout, sync=_sync, force=False):
    global _nutmegCore

    if _nutmegCore is None or force:
        _nutmegCore = Nutmeg(address, pub_port, sub_port, timeout, sync)

    elif address != _address or \
            pub_port != _pubport or \
            sub_port != _subport or \
            timeout != _timeout or \
            sync != _sync:
        print("WARNING: Module's Nutmeg core already exists with different settings. For multiple instances, manually instantiate a Nutmeg.Nutmeg(...) object.")

    return _nutmegCore


def initialized():
    return _nutmegCore is not None and _nutmegCore.initialized


def figure(handle, figureDef):
    return _core().figure(handle, figureDef)


def check_errors():
    _core().check_errors()


def wait_for_nutmeg():
    _core().wait_for_nutmeg()


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


class NutmegError(Exception):
    def __init__(self, name, message, **kwargs):
        self.name = name
        self.message = message


class NutmegException(Exception):
    def __init__(self, errors):
        self.errors = errors
        self.messages = [ '\t{}: {}'.format(err.name, err.message) for err in errors ]
        self.message = "One or more errors in Nutmeg:\n" + '\n\n'.join(self.messages)
        Exception.__init__(self, self.message)


# Threaded decorator
def _threaded(fn):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=fn, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
    return wrapper


class Nutmeg:

    def __init__(self, address=_address, pub_port=_pubport, sub_port=_subport, timeout=_timeout, sync=_sync, pingperiod=10000):
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
        self.sync = sync

        self.task_count = 0
        self.session = uuid.uuid1()
        self.session_str = '{{{}}}'.format(self.session)
        self.session_bytes = bytes( self.session_str, 'utf-8' )

        # Create the socket and connect to it.
        self.running = True
        self.context = zmq.Context()
        self.pubsock = None
        self.subsock = None
        self.poller = None
        self.reset_sub = False
        self.sub_running = False

        self.socket_lock = threading.Lock()
        self.state_lock = threading.Lock()

        self.figures = {}
        self.parameters = {}

        self.tasks = {}
        self.state = OrderedDict()
        self.error_queue = queue.Queue()
        self.state_requested = False
        self.first_good_task = -1

        # Fire it up
        self.reset_socket()

    def reset_socket(self):
        # Close things if necessary
        if self.pubsock is not None:
            self.pubsock.close()

        print("Nutmeg connecting")
        print("\tPublishing to:", self.pub_address)
        self.pubsock = self.context.socket(zmq.PUB)
        # self.socket.setsockopt(zmq.LINGER, 0)
        self.pubsock.connect(self.pub_address)

        if not self.sub_running:
            self.sub_running = True
            self._subscribe()
        else:
            self.reset_sub = True

        # # Last time a disconnection occurred
        # self.disconnected_t = time.time()
        self.running = True
        self._poke_server()

    def ready(self):
        return self.running

    @_threaded
    def _poke_server(self):
        '''
        Poke the server until we get a pong, or the state is requested.
        '''
        msg = dict(command="Ping", target="", args=[])
        for i in range(100):
            task = self.publish_message(msg)
            if self.state_requested or task.wait(0.01):
                break

    @_threaded
    def _subscribe(self):
        while True:
            if self.subsock is None:
                self.subsock = self.context.socket(zmq.SUB)
                # Subscribe to 'Nutmeg' and this session's unique ID
                # 'Nutmeg' is used when attempting to communicate to all connected clients.
                self.subsock.setsockopt(zmq.SUBSCRIBE, b'Nutmeg')
                self.subsock.setsockopt(zmq.SUBSCRIBE, self.session_bytes)
                # Time out 100ms when checking if there are new messages coming in:
                self.subsock.RCVTIMEO = 100
                self.subsock.connect(self.sub_address)
                print('\tSubscribed to: "{}"'.format(self.session_str))

            try:
                # Logic for reading in new messages
                full_msg = []
                full_msg.append(self.subsock.recv())
                full_msg.append(self.subsock.recv_json())
                while self.subsock.getsockopt(zmq.RCVMORE):
                    full_msg.append(self.subsock.recv())

            except zmq.ZMQError as e:
                # Time out to allow us to check if the sub socket needs to be closed
                if e.errno != zmq.EAGAIN:
                    # Not a timeout (not good)
                    print("Error in ZMQ")
                    print(e)
                    time.sleep(1)
            else:
                # Process the message that was received
                msg = full_msg[1]
                mtype = msg['messageType']

                if mtype == 'parameterUpdated':
                    self.update_parameter(msg)

                elif mtype == 'requestState':
                    print("State Requested from:", full_msg[0])
                    self.first_good_task = self.task_count
                    self.state_requested = True
                    self.send_state()

                elif mtype == 'success':
                    self._task_done(msg['id'])

                elif mtype == 'error':
                    if msg['errorName'] == 'FigureNotFoundError':
                        print("WARNING: Figure doesn't exist", self.state_requested)
                        guitarget = '{}.GUI'.format( msg['details']['figureName'] )
                        print("Gui:", guitarget)
                        if guitarget in self.state:
                            self.publish_message(self.state[guitarget])
                        # if self.state_requested:
                        #     self.send_state()
                    # if self.state_requested and msg['id'] >= self.first_good_task:
                    else:
                        self._task_done(msg['id'])
                        self.error_queue.put(msg)

            if self.reset_sub:
                # Close and delete the socket if a reset was flagged
                self.subsock.close()
                self.subsock = None
                self.reset_sub = False

    def check_errors(self):
        errors = []
        while not self.error_queue.empty():
            err_msg = self.error_queue.get_nowait()
            errors.append( NutmegError(err_msg['errorName'], err_msg['message']) )

        if len(errors) > 0:
            raise NutmegException(errors)

    def _task_done(self, task_id):
        try:
            task = self.tasks.pop(task_id)
            task.done.set()
        except KeyError:
            pass

    def update_state(self, msg, target=None):
        self.state_lock.acquire()

        try:
            if target is None:
                target = msg['target']
            if target in self.state:
                del self.state[target]

            self.state[target] = msg

        finally:
            self.state_lock.release()

    def update_parameter(self, msg):
        figure_handle = msg['figureHandle']
        name = msg['parameter']
        param = self.parameter(figure_handle, name)
        param.update_value(msg['value'])

        # Put this in the current state so that it is restored
        target = '{}.{}.value'.format(figure_handle, name)
        msg = dict(command="SetParam", target=target, args=[msg['value']])
        self.update_state(msg)

    def _publish(self, msg, binary_data):
        # Check socketlock
        self.socket_lock.acquire()
        try:
            # Inject task ID (thread safe in here)
            task = Task(self, self.task_count)
            self.tasks[self.task_count] = task
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

            return task

        except IOError:
            raise

        finally:
            self.socket_lock.release()

    def publish_message(self, msg):
        '''
        Process the message for numpy arrays and convert them to Nutmeg-ready
        message data before sending
        '''
        self.check_errors()
        if not self.state_requested and msg['command'] != 'Ping':
            task = Task(self, -1)
            task.done.set()
            return task

        msg, binary_header, binary_data = to_nutmeg_message(msg)
        msg['binary'] = binary_header
        msg['session'] = self.session_str

        return self._publish(msg, binary_data)

    def ping(self, sync=None):
        if sync is None:
            sync = self.sync

        msg = dict(command="Ping", target="", args=[])
        task = self.publish_message(msg)

        if sync:
            task.wait()

    def figure(self, handle, figureDef):
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
        self.update_state(msg)
        task = self.publish_message(msg)

        fig = Figure(self, handle, address=self.host, pub_port=self.pub_port, qml=qml)

        if self.sync:
            task.wait()
            self.check_errors()

        return fig

    def set_gui(self, handle, qml):
        '''
        Set the Gui definition of the Figure with handle, `handle`.
        :param handle: Handle of Figure
        :param qml: Full QML definition of Gui
        '''
        msg = dict(command="SetGui", target=handle, args=[qml])
        self.update_state(msg, target='{}.GUI'.format(handle))
        task = self.publish_message(msg)

        if self.sync:
            task.wait()
            self.check_errors()

    def set_property(self, handle, value, sync=None):
        '''
        Set property at handle
        '''
        if sync is None:
            sync = self.sync

        msg = dict(command="SetProperty", target=handle, args=[value])
        self.update_state(msg)
        task = self.publish_message(msg)

        if sync:
            task.wait()
            self.check_errors()
        return task

    def set_properties(self, handle, **properties):
        tasks = []
        for name, value in properties.items():
            tasks.append( self.set_property('.'.join((handle, name)), value, False) )

        if self.sync:
            for task in tasks:
                task.wait()
            self.check_errors()

    def invoke_method(self, handle, *args, **kwargs):
        if 'sync' in kwargs and kwargs['sync'] is not None:
            sync = kwargs['sync']
        else:
            sync = self.sync

        msg = dict(command="Invoke", target=handle, args=args)
        task = self.publish_message(msg)

        if sync:
            task.wait()
            self.check_errors()
        return task

    def set_parameter(self, handle, value, sync=None):
        '''
        Set Gui property at handle
        '''
        if sync is None:
            sync = self.sync

        msg = dict(command="SetParam", target=handle, args=[value])
        self.update_state(msg)
        task = self.publish_message(msg)

        if sync:
            task.wait()
            self.check_errors()

    def set_parameters(self, handle, **params):
        tasks = []
        for name, value in params.items():
            tasks.append( self.set_parameter('.'.join((handle, name)), value, False) )

        if self.sync:
            for task in tasks:
                task.wait()
            self.check_errors()

    def parameter(self, handle, param):
        key = '.'.join((handle, param))
        if key not in self.parameters:
            self.parameters[key] = Parameter(handle, param, nutmeg=self)

        return self.parameters[key]

    def send_state(self):
        '''
        Send a full update of the current local state of properties and figures.
        This excludes any method invokations.
        '''
        self.state_lock.acquire()

        try:
            for target, msg in self.state.items():
                print("Updating state for:", target)
                self.publish_message(msg)

        finally:
            self.state_lock.release()

    def wait_for_nutmeg(self, timeout=10):
        t0 = time.time()
        while not self.state_requested and time.time() - t0 < timeout:
            time.sleep(0.1)
        time.sleep(0.5)
        self.check_errors()


class Task(object):
    def __init__(self, nutmeg, task_id):
        self.nutmeg = nutmeg
        self.task_id = task_id

        self.done = threading.Event()
        self.done.clear()

    def wait(self, timeout=None):
        return self.done.wait(timeout)


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

    def set_gui(self, guiDef):
        # We're going by the interesting assumption that a file path cannot be
        # used to define a QML layout...
        qml = ""
        if os.path.exists(guiDef):
            with open(guiDef, 'r') as F:
                qml = F.read()  #.encode('UTF-8')
        else:
            qml = guiDef

        self.nutmeg.set_gui(self.handle, qml)

    def parameter(self, param):
        return self.nutmeg.parameter(self.handle, param)

    def set(self, handle, *value, **properties):
        '''
        Set the properties in `handle` according to the provided dictionary of
        properties and their values.

        For example:
        ```figure.set('ax.data.x', range(10))```
        or
        ```figure.set('ax.data', x=range(10), y=someData})```

        :param handle: A string to the object or property of interest
        :param *value: The first value is used to set the property at `handle`. If this is empty, **properties is used
        :param **properties: Each keyword describes a property in `handle` to set. These properties are set the values paired to their respective keywords.
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

    def invoke(self, handle, *args):
        '''
        Invoke method in at the given location with args.

        For example:
        ```figure.set('ax.data.append', [0,1,2], [5,0,9])```

        :param handle: String with "address" to method.
        :param *args: Arguments for method
        '''
        full_handle = self.handle + "." + handle
        self.nutmeg.invoke_method(full_handle, *args)


class Parameter():
    '''
    Keep track of a parameter's value and state.
    '''
    def __init__(self, figure_handle, name, value=0, changed=0, nutmeg=None):
        self.figure_handle = figure_handle
        self.name = name
        self.value = value
        self._changed = changed
        self.callbacks = []
        self.valueLock = threading.Lock()
        self.changedLock = threading.Lock()
        self.nutmeg = nutmeg

    @property
    def changed(self):
        return self._changed

    @changed.setter
    def changed(self, value):
        self.changedLock.acquire()
        self._changed = value
        if value:
            for callback in self.callbacks:
                callback()
        self.changedLock.release()

    def update_value(self, value):
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

    def read_changed(self):
        '''
        Return whether the value of the parameter has changed, and set changed to false.
        Useful for Button types where you might not care about the actual value.
        '''
        value = self.changed
        self.changed = 0
        return value

    def set(self, *value, **properties):
        if self.nutmeg is None:
            print("WARNING: Core Nutmeg object of parameter is None")
            return

        if len(value) == 0 and len(properties) == 0:
            return

        if len(value) > 0 and len(properties) > 0:
            print("WARNING: Keyword properties of parameter.set() override ordered args.")

        if len(properties) > 0:
            target = '{}.{}'.format(self.figure_handle, self.name)
            self.nutmeg.set_parameters(target, **properties)

        elif len(value) > 0:
            target = '{}.{}.value'.format(self.figure_handle, self.name)
            self.nutmeg.set_parameter(target, value[0])

    def register_callback(self, callback):
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
