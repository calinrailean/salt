# -*- coding: utf-8 -*-
'''
    salt.log.handlers
    ~~~~~~~~~~~~~~~~~

    .. versionadded:: 0.17.0

    Custom logging handlers to be used in salt.
'''
from __future__ import absolute_import, print_function, unicode_literals

# Import python libs
import sys
import copy
import logging
import threading
import collections
import logging.handlers

# Import salt libs
from salt.log.mixins import NewStyleClassMixIn, ExcInfoOnLogLevelFormatMixIn
from salt.ext.six.moves import queue

log = logging.getLogger(__name__)


if sys.version_info < (2, 7):
    # Since the NullHandler is only available on python >= 2.7, here's a copy
    # with NewStyleClassMixIn so it's also a new style class
    class NullHandler(logging.Handler, NewStyleClassMixIn):
        '''
        This is 1 to 1 copy of python's 2.7 NullHandler
        '''
        def handle(self, record):
            pass

        def emit(self, record):
            pass

        def createLock(self):  # pylint: disable=C0103
            self.lock = None

    logging.NullHandler = NullHandler


class TemporaryLoggingHandler(logging.NullHandler):
    '''
    This logging handler will store all the log records up to its maximum
    queue size at which stage the first messages stored will be dropped.

    Should only be used as a temporary logging handler, while the logging
    system is not fully configured.

    Once configured, pass any logging handlers that should have received the
    initial log messages to the function
    :func:`TemporaryLoggingHandler.sync_with_handlers` and all stored log
    records will be dispatched to the provided handlers.

    .. versionadded:: 0.17.0
    '''

    def __init__(self, level=logging.NOTSET, max_queue_size=100000):
        super(TemporaryLoggingHandler, self).__init__(level=level)
        self.__messages = collections.deque(maxlen=max_queue_size)

    def handle(self, record):
        self.acquire()
        self.__messages.append(record)
        self.release()

    def sync_with_handlers(self, handlers=()):
        '''
        Sync the stored log records to the provided log handlers.
        '''
        if not handlers:
            return

        while self.__messages:
            record = self.__messages.popleft()
            for handler in handlers:
                if handler.level > record.levelno:
                    # If the handler's level is higher than the log record one,
                    # it should not handle the log record
                    continue
                handler.handle(record)


class StreamHandler(ExcInfoOnLogLevelFormatMixIn, logging.StreamHandler, NewStyleClassMixIn):
    '''
    Stream handler which properly handles exc_info on a per handler basis
    '''


class FileHandler(ExcInfoOnLogLevelFormatMixIn, logging.FileHandler, NewStyleClassMixIn):
    '''
    File handler which properly handles exc_info on a per handler basis
    '''


class SysLogHandler(ExcInfoOnLogLevelFormatMixIn, logging.handlers.SysLogHandler, NewStyleClassMixIn):
    '''
    Syslog handler which properly handles exc_info on a per handler basis
    '''
    def handleError(self, record):
        '''
        Override the default error handling mechanism for py3
        Deal with syslog os errors when the log file does not exist
        '''
        handled = False
        if sys.stderr and sys.version_info >= (3, 5, 4):
            t, v, tb = sys.exc_info()
            if t.__name__ in 'FileNotFoundError':
                sys.stderr.write('[WARNING ] The log_file does not exist. Logging not setup correctly or syslog service not started.\n')
                handled = True

        if not handled:
            super(SysLogHandler, self).handleError(record)


class RotatingFileHandler(ExcInfoOnLogLevelFormatMixIn, logging.handlers.RotatingFileHandler, NewStyleClassMixIn):
    '''
    Rotating file handler which properly handles exc_info on a per handler basis
    '''
    def handleError(self, record):
        '''
        Override the default error handling mechanism

        Deal with log file rotation errors due to log file in use
        more softly.
        '''
        handled = False

        # Can't use "salt.utils.platform.is_windows()" in this file
        if (sys.platform.startswith('win') and
                logging.raiseExceptions and
                sys.stderr):  # see Python issue 13807
            exc_type, exc, exc_traceback = sys.exc_info()
            try:
                # PermissionError is used since Python 3.3.
                # OSError is used for previous versions of Python.
                if exc_type.__name__ in ('PermissionError', 'OSError') and exc.winerror == 32:
                    if self.level <= logging.WARNING:
                        sys.stderr.write('[WARNING ] Unable to rotate the log file "{0}" '
                                         'because it is in use\n'.format(self.baseFilename)
                        )
                    handled = True
            finally:
                # 'del' recommended. See documentation of
                # 'sys.exc_info()' for details.
                del exc_type, exc, exc_traceback

        if not handled:
            super(RotatingFileHandler, self).handleError(record)


if sys.version_info > (2, 6):
    class WatchedFileHandler(ExcInfoOnLogLevelFormatMixIn, logging.handlers.WatchedFileHandler, NewStyleClassMixIn):
        '''
        Watched file handler which properly handles exc_info on a per handler basis
        '''


if sys.version_info < (3, 2):
    class QueueHandler(ExcInfoOnLogLevelFormatMixIn, logging.Handler, NewStyleClassMixIn):
        '''
        This handler sends events to a queue. Typically, it would be used together
        with a multiprocessing Queue to centralise logging to file in one process
        (in a multi-process application), so as to avoid file write contention
        between processes.

        This code is new in Python 3.2, but this class can be copy pasted into
        user code for use with earlier Python versions.
        '''

        def __init__(self, queue):
            '''
            Initialise an instance, using the passed queue.
            '''
            logging.Handler.__init__(self)
            self.queue = queue

        def enqueue(self, record):
            '''
            Enqueue a record.

            The base implementation uses put_nowait. You may want to override
            this method if you want to use blocking, timeouts or custom queue
            implementations.
            '''
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                sys.stderr.write('[WARNING ] Message queue is full, '
                                 'unable to write "{0}" to log'.format(record))

        def prepare(self, record):
            '''
            Prepares a record for queuing. The object returned by this method is
            enqueued.
            The base implementation formats the record to merge the message
            and arguments, and removes unpickleable items from the record
            in-place.
            You might want to override this method if you want to convert
            the record to a dict or JSON string, or send a modified copy
            of the record while leaving the original intact.
            '''
            # The format operation gets traceback text into record.exc_text
            # (if there's exception data), and also returns the formatted
            # message. We can then use this to replace the original
            # msg + args, as these might be unpickleable. We also zap the
            # exc_info and exc_text attributes, as they are no longer
            # needed and, if not None, will typically not be pickleable.
            msg = self.format(record)
            # bpo-35726: make copy of record to avoid affecting other handlers in the chain.
            record = copy.copy(record)
            record.message = msg
            record.msg = msg
            record.args = None
            record.exc_info = None
            record.exc_text = None
            return record

        def emit(self, record):
            '''
            Emit a record.

            Writes the LogRecord to the queue, preparing it for pickling first.
            '''
            try:
                self.enqueue(self.prepare(record))
            except Exception:
                self.handleError(record)
elif sys.version_info < (3, 7):
    # On python versions lower than 3.7, we sill subclass and overwrite prepare to include the fix for:
    #  https://bugs.python.org/issue35726
    class QueueHandler(ExcInfoOnLogLevelFormatMixIn, logging.handlers.QueueHandler):  # pylint: disable=no-member,E0240

        def enqueue(self, record):
            '''
            Enqueue a record.

            The base implementation uses put_nowait. You may want to override
            this method if you want to use blocking, timeouts or custom queue
            implementations.
            '''
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                sys.stderr.write('[WARNING ] Message queue is full, '
                                 'unable to write "{0}" to log'.format(record))

        def prepare(self, record):
            '''
            Prepares a record for queuing. The object returned by this method is
            enqueued.
            The base implementation formats the record to merge the message
            and arguments, and removes unpickleable items from the record
            in-place.
            You might want to override this method if you want to convert
            the record to a dict or JSON string, or send a modified copy
            of the record while leaving the original intact.
            '''
            # The format operation gets traceback text into record.exc_text
            # (if there's exception data), and also returns the formatted
            # message. We can then use this to replace the original
            # msg + args, as these might be unpickleable. We also zap the
            # exc_info and exc_text attributes, as they are no longer
            # needed and, if not None, will typically not be pickleable.
            msg = self.format(record)
            # bpo-35726: make copy of record to avoid affecting other handlers in the chain.
            record = copy.copy(record)
            record.message = msg
            record.msg = msg
            record.args = None
            record.exc_info = None
            record.exc_text = None
            return record
else:
    class QueueHandler(ExcInfoOnLogLevelFormatMixIn, logging.handlers.QueueHandler):  # pylint: disable=no-member,E0240

        def enqueue(self, record):
            '''
            Enqueue a record.

            The base implementation uses put_nowait. You may want to override
            this method if you want to use blocking, timeouts or custom queue
            implementations.
            '''
            try:
                self.queue.put_nowait(record)
            except queue.Full:
                sys.stderr.write('[WARNING ] Message queue is full, '
                                 'unable to write "{0}" to log'.format(record))
