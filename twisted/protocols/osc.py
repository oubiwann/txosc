# -*- test-case-name: twisted.test.test_osc -*-
# Copyright (c) 2009 Alexandre Quessy, Arjan Scherpenisse
# See LICENSE for details.

"""
OSC 1.1 Protocol over UDP for Twisted.
Specification : http://opensoundcontrol.org/spec-1_1
Examples : http://opensoundcontrol.org/spec-1_0-examples
"""
import string
import math
import struct
import time

from twisted.internet.protocol import DatagramProtocol
from twisted.internet import reactor
from twisted.internet import defer

def _ceilToMultipleOfFour(num):
    """
    Rounds a number to the closest higher number that is a mulitple of four.
    That is for data that need to be padded with zeros so that the length of their data
    must be a multiple of 32 bits.
    """
    #return math.ceil((num + 1) / 4.0) * 4
    return num + (4 - (num % 4)) 

class OscError(Exception):
    """
    Any error raised by this module.
    """
    pass


class Message(object):
    """
    OSC Message
    """

    def __init__(self, address, *arguments):
        self.address = address
        self.arguments = list(arguments)

    def toBinary(self):
        return StringArgument(self.address).toBinary() + "," + self.getTypeTags() + "".join([a.toBinary() for a in self.arguments])

    def getTypeTags(self, padWithZeros=True):
        """
        :rettype: string
        """
        s = "".join([a.typeTag for a in self.arguments])
        if padWithZeros:
            length = len(s)
            pad = _ceilToMultipleOfFour(length) - length
            s += "\0" * pad
        return s    


    @staticmethod
    def fromBinary(data):
        global _tags
        osc_address, leftover = StringArgument.strFromBinary(data)
        #print("Got OSC address: %s" % (osc_address))
        message = Message(osc_address)
        type_tags, leftover = StringArgument.strFromBinary(leftover)
        if type_tags != ",": # no arg
            for type_tag in type_tags[1:]:
                arg, leftover = _tags[type_tag].fromBinary(leftover) 
                message.arguments.append(arg)
        return message, leftover
                
    def __str__(self):
        """
        For debugging purposes
        """
        args = " ".join([str(a) for a in self.arguments])
        return "%s ,%s %s" % (self.address, self.getTypeTags(False), args)

class Bundle(object):
    """
    OSC Bundle
    """
    def __init__(self, messages=[],  time_tag=0):
        self.messages = messages
        self.time_tag = time_tag
        if self.time_tag is None:
            pass
            #TODO create time tag

    def toBinary(self):
        data = "#bundle"
        data += TimeTagArgument(self.time_tag).toBinary()
        for msg in self.messages:
            binary = msg.toBinary()
            data += IntArgument(len(binary)).toBinary()
            data += binary
        return data


class Argument(object):
    """
    Base OSC argument
    """
    typeTag = None  # Must be implemented in children classes

    def __init__(self, value):
        self.value = value


    def toBinary(self):
        """
        Encode the value to binary form, ready to send over the wire.
        """
        raise NotImplemented('Override this method')


    @staticmethod
    def fromBinary(data):
        """
        Decode the value from binary form. Result is a tuple of (Instance, leftover).
        """
        raise NotImplemented('Override this method')


    def __str__(self):
        """
        For debugging purposes
        """
        return "%s:%s" % (self.typeTag, self.value)

#
# OSC 1.1 required arguments
#

class BlobArgument(Argument):
    typeTag = "b"

    def toBinary(self):
        sz = len(self.value)
        #length = math.ceil((sz+1) / 4.0) * 4
        length = _ceilToMultipleOfFour(sz)
        return struct.pack(">i%ds" % (length), sz, str(self.value))
    
    @staticmethod
    def fromBinary(data):
        try:
            length = struct.unpack(">i", data[0:4])[0]
            index_of_leftover = _ceilToMultipleOfFour(length) + 4
            try:
                blob_data = data[4:length + 4]
            except IndexError, e:
                raise OscError("Not enough bytes to find size of a blob of size %s in %s." % (length, data))
        except IndexError, e:
            raise OscError("Not enough bytes to find size of a blob argument in %s." % (data))
        leftover = data[index_of_leftover:]
        return BlobArgument(blob_data), leftover
        


class StringArgument(Argument):
    typeTag = "s"

    def toBinary(self):
        length = math.ceil((len(self.value)+1) / 4.0) * 4
        return struct.pack(">%ds" % (length), str(self.value))

    @staticmethod
    def fromBinary(data):
        """
        Parses binary data to get the first string in it.

        Returns a tuple with StringArgument instance, leftover.
        The leftover should be parsed next.
        :rettype: tuple
        """
        s, leftover = StringArgument.strFromBinary(data)
        return StringArgument(s), leftover

    @staticmethod
    def strFromBinary(data):
        """
        Parses binary data to get the first string in it.

        Returns a tuple with string, leftover.
        The leftover should be parsed next.
        :rettype: tuple

        OSC-string A sequence of non-null ASCII characters followed by a null, 
            followed by 0-3 additional null characters to make the total number of bits a multiple of 32.
        
        Strings are used so often in OSC, that there's a need for a 
        class method that does only this, and not return a 
        StringArgument, just a str. the fromBinary class method calls this
        one and wraps the first element of the tuple in a StringArgument.
        """
        null_pos = string.find(data, "\0") # find the first null char
        s = data[0:null_pos] # get the first string out of data
        i = null_pos # find the position of the beginning of the next data
        i = _ceilToMultipleOfFour(i)
        leftover = data[i:]
        return s, leftover
    

class IntArgument(Argument):
    typeTag = "i"

    def toBinary(self):
        return struct.pack(">i", int(self.value))

    @staticmethod
    def fromBinary(data):
        try:
            i = struct.unpack(">i", data[:4])[0]
            leftover = data[4:]
        except IndexError, e:
            raise OscError("Too few bytes left to get an int from %s." % (data))
            #FIXME: do not raise error and return leftover anyways ?
        return IntArgument(i), leftover


class FloatArgument(Argument):
    typeTag = "f"

    def toBinary(self):
        return struct.pack(">f", float(self.value))

    @staticmethod
    def fromBinary(data):
        try:
            f = struct.unpack(">f", data[:4])[0]
            leftover = data[4:]
        except IndexError, e:
            raise OscError("Too few bytes left to get a float from %s." % (data))
            #FIXME: do not raise error and return leftover anyways ?
        return FloatArgument(f), leftover

class TimeTagArgument(Argument):
    """
    Time tags are represented by a 64 bit fixed point number. The first 32 bits specify the number of seconds since midnight on January 1, 1900, and the last 32 bits specify fractional parts of a second to a precision of about 200 picoseconds. This is the representation used by Internet NTP timestamps. 

    The time tag value consisting of 63 zero bits followed by a one in the least signifigant bit is a special case meaning "immediately."
    """
    typeTag = "t"
    SECONDS_UTC_TO_UNIX_EPOCH = 2208988800

    def __init__(self, value=None):
        # TODO: call parent's constructor ?
        if value is None:
            #FIXME: is that the correct NTP timestamp ?
            value = self.SECONDS_UTC_TO_UNIX_EPOCH + time.time()
        self.value = value

    def toBinary(self):
        fr, sec = math.modf(self.value)
        return struct.pack('>ll', long(sec), long(fr * 1e9))


class BooleanArgument(Argument):
    def __init__(self, value):
        Argument.__init__(self, value)
        if self.value:
            self.typeTag = "T"
        else:
            self.typeTag = "F"

    def toBinary(self):
        return "" # bool args do not have data, just a type tag


class NullArgument(Argument):
    typeTag = "N"

    def __init__(self):
        # TODO: call parent's constructor ?
        self.value = None


class ImpulseArgument(Argument):
    typeTag = "I"

    def __init__(self):
        # TODO: call parent's constructor ?
        self.value = None

#
# Optional arguments
#
# Should we implement all types that are listed "optional" in
# http://opensoundcontrol.org/spec-1_0 ?

#class SymbolArgument(StringArgument):
#    typeTag = "S"


#global dicts
_types = {
    float: FloatArgument,
    str: StringArgument,
    int: IntArgument,
    #TODO: unicode: StringArgument,
    #TODO : more types
    }


_tags = {
    "b": BlobArgument,
    "f": FloatArgument,
    "i": IntArgument,
    "s": StringArgument,
    #TODO : more types
    }

def createArgument(data, type_tag=None):
    """
    Creates an OSC argument, trying to guess its type if no type is given.

    Factory of *Attribute object.
    :param data: Any Python base type.
    :param type_tag: One-letter string. Either "i", "f", etc.
    """
    global _types
    global _tags
    kind = type(data)
    try:
        if type_tag in _tags.keys():
            return _tags[type_tag](data)
        if kind in _types.keys():
            return _types[kind](data)
        else:
            raise OscError("Data %s")
    except ValueError, e:
        raise OscError("Could not cast %s to %s. %s" % (data, type_tag, e.message))


class OscProtocol(DatagramProtocol):
    """
    The OSC server protocol
    """
    def datagramReceived(self, data, (host, port)):
        #The contents of an OSC packet must be either an OSC Message or an OSC Bundle. The first byte of the packet's contents unambiguously distinguishes between these two alternatives.
        #packet_type = data[0] # TODO
        print("received %r from %s:%d" % (data, host, port))
        #TODO : check if it is a #bundle
        message = Message.fromBinary(data)
        #self.transport.write(data, (host, port))
        


class OscClientProtocol(DatagramProtocol):
     def __init__(self, onStart):
         self.onStart = onStart

     def startProtocol(self):
         self.onStart.callback(self)


class OscSender(object):
     def __init__(self):
         d = defer.Deferred()
         def listening(proto):
             self.proto = proto
         d.addCallback(listening)
         self._port = reactor.listenUDP(0, OscClientProtocol(d))

     def send(self, msg, (host, port)):
         data = msg.toBinary()
         self.proto.transport.write(data, (host, port))

     def stop(self):
         self._call.stop()
         self._port.stopListening()


# TODO: move to doc/core/examples/oscserver.py
if __name__ == "__main__":
    reactor.listenUDP(17777, OscProtocol())

    ds = OscSender()
    ds.send(Message("/foo"), ("127.0.0.1", 17777))

    reactor.run()
