"""
Transport layer for Polaris Harness.

Implements TCP/HTTP transport for protocol communication.
"""

import socket
import threading
import time
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum

from .framing import FramingEngine, Frame, FramingError
from .clock import ClockManager


class ConnectionState(Enum):
    """Connection state enumeration."""
    OPEN = "open"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class Connection:
    """Connection data structure."""
    connection_id: str
    socket: socket.socket
    address: tuple
    state: ConnectionState = ConnectionState.OPEN
    framing_engine: FramingEngine = None
    metadata: Dict[str, Any] = None


class TransportError(Exception):
    """Raised when transport operations fail."""

    def __init__(self, message: str, connection_id: str = None):
        super().__init__(message)
        self.connection_id = connection_id


class TCPServer:
    """TCP server for protocol communication."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, clock: ClockManager = None):
        self.host = host
        self.port = port
        self.clock = clock or ClockManager()
        self.connections: Dict[str, Connection] = {}
        self.server_socket = None
        self.running = False
        self.listener_thread = None
        self.connection_threads = {}
        self.event_handlers: Dict[str, List[Callable]] = {}

    def start(self) -> bool:
        """Start the TCP server.

        Returns:
            True if server started successfully, False otherwise
        """
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)

            self.running = True
            self.listener_thread = threading.Thread(target=self._accept_connections)
            self.listener_thread.daemon = True
            self.listener_thread.start()

            return True
        except Exception as e:
            raise TransportError(f"Failed to start TCP server: {e}")

    def stop(self) -> None:
        """Stop the TCP server."""
        self.running = False

        # Close all connections
        for connection_id, connection in self.connections.items():
            connection.state = ConnectionState.CLOSED
            try:
                connection.socket.close()
            except:
                pass

        self.connections.clear()

        # Close server socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass

        # Wait for threads to finish
        if self.listener_thread:
            self.listener_thread.join(timeout=5.0)

        for thread in self.connection_threads.values():
            thread.join(timeout=5.0)

    def _accept_connections(self) -> None:
        """Accept incoming connections."""
        while self.running:
            try:
                self.server_socket.settimeout(1.0)
                client_socket, address = self.server_socket.accept()

                connection_id = str(hash((address, time.time())))
                connection = Connection(
                    connection_id=connection_id,
                    socket=client_socket,
                    address=address,
                    metadata={"host": self.host, "port": self.port}
                )

                self.connections[connection_id] = connection

                # Start connection handler thread
                thread = threading.Thread(
                    target=self._handle_connection,
                    args=(connection_id,)
                )
                thread.daemon = True
                thread.start()
                self.connection_threads[connection_id] = thread

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    raise TransportError(f"Error accepting connection: {e}")

    def _handle_connection(self, connection_id: str) -> None:
        """Handle a connection.

        Args:
            connection_id: Connection identifier
        """
        connection = self.connections.get(connection_id)
        if not connection:
            return

        try:
            while self.running and connection.state == ConnectionState.OPEN:
                # Receive data
                self.server_socket.settimeout(1.0)
                data = connection.socket.recv(4096)

                if not data:
                    # Connection closed
                    connection.state = ConnectionState.CLOSED
                    break

                # Process data
                self._process_connection_data(connection_id, data)

        except socket.timeout:
            pass
        except Exception as e:
            if self.running:
                connection.state = ConnectionState.ERROR
                raise TransportError(f"Error handling connection {connection_id}: {e}", connection_id)
        finally:
            # Clean up connection
            connection.state = ConnectionState.CLOSED
            try:
                connection.socket.close()
            except:
                pass

            if connection_id in self.connections:
                del self.connections[connection_id]
            if connection_id in self.connection_threads:
                del self.connection_threads[connection_id]

    def _process_connection_data(self, connection_id: str, data: bytes) -> None:
        """Process data from a connection.

        Args:
            connection_id: Connection identifier
            data: Data to process
        """
        connection = self.connections.get(connection_id)
        if not connection or not connection.framing_engine:
            return

        try:
            # Feed data to framing engine
            frames = connection.framing_engine.feed(data)

            # Process frames
            for frame in frames:
                self._process_frame(connection_id, frame)

            # Check for partial frame
            partial_frame = connection.framing_engine.get_partial_frame()
            if partial_frame:
                self._process_partial_frame(connection_id, partial_frame)

        except FramingError as e:
            self._handle_framing_error(connection_id, e)
        except Exception as e:
            self._handle_transport_error(connection_id, e)

    def _process_frame(self, connection_id: str, frame: Frame) -> None:
        """Process a complete frame.

        Args:
            connection_id: Connection identifier
            frame: Frame to process
        """
        # Emit event
        self._emit_event("frame_received", {
            "connection_id": connection_id,
            "frame": frame,
            "endpoint": "command"
        })

    def _process_partial_frame(self, connection_id: str, frame: Frame) -> None:
        """Process a partial frame.

        Args:
            connection_id: Connection identifier
            frame: Partial frame
        """
        # Emit event
        self._emit_event("partial_frame_received", {
            "connection_id": connection_id,
            "frame": frame,
            "endpoint": "command"
        })

    def _handle_framing_error(self, connection_id: str, error: FramingError) -> None:
        """Handle a framing error.

        Args:
            connection_id: Connection identifier
            error: Framing error
        """
        # Emit event
        self._emit_event("framing_error", {
            "connection_id": connection_id,
            "error": error
        })

    def _handle_transport_error(self, connection_id: str, error: Exception) -> None:
        """Handle a transport error.

        Args:
            connection_id: Connection identifier
            error: Transport error
        """
        # Emit event
        self._emit_event("transport_error", {
            "connection_id": connection_id,
            "error": error
        })

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit an event.

        Args:
            event_type: Event type
            data: Event data
        """
        if event_type in self.event_handlers:
            for handler in self.event_handlers[event_type]:
                try:
                    handler(data)
                except Exception as e:
                    print(f"Error in event handler {event_type}: {e}")

    def add_event_handler(self, event_type: str, handler: Callable) -> None:
        """Add an event handler.

        Args:
            event_type: Event type
            handler: Event handler
        """
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)

    def remove_event_handler(self, event_type: str, handler: Callable) -> None:
        """Remove an event handler.

        Args:
            event_type: Event type
            handler: Event handler
        """
        if event_type in self.event_handlers:
            try:
                self.event_handlers[event_type].remove(handler)
            except ValueError:
                pass

    def get_connection(self, connection_id: str) -> Optional[Connection]:
        """Get a connection.

        Args:
            connection_id: Connection identifier

        Returns:
            Connection or None
        """
        return self.connections.get(connection_id)

    def get_connections(self) -> Dict[str, Connection]:
        """Get all connections.

        Returns:
            Dictionary of connections
        """
        return self.connections.copy()

    def get_port(self) -> int:
        """Get the port the server is listening on.

        Returns:
            Port number
        """
        if self.server_socket:
            return self.server_socket.getsockname()[1]
        return self.port


class TransportManager:
    """Manages transport instances."""

    def __init__(self):
        self.servers: Dict[str, TCPServer] = {}
        self.clock = ClockManager()

    def create_server(self, server_id: str, host: str = "127.0.0.1", port: int = 0) -> TCPServer:
        """Create a TCP server.

        Args:
            server_id: Server identifier
            host: Host to bind to
            port: Port to bind to

        Returns:
            TCP server
        """
        server = TCPServer(host, port, self.clock)
        self.servers[server_id] = server
        return server

    def get_server(self, server_id: str) -> Optional[TCPServer]:
        """Get a server.

        Args:
            server_id: Server identifier

        Returns:
            TCP server or None
        """
        return self.servers.get(server_id)

    def remove_server(self, server_id: str) -> None:
        """Remove a server.

        Args:
            server_id: Server identifier
        """
        server = self.servers.get(server_id)
        if server:
            server.stop()
            del self.servers[server_id]

    def stop_all(self) -> None:
        """Stop all servers."""
        for server in self.servers.values():
            server.stop()
        self.servers.clear()


# Global transport manager
_transport_manager = TransportManager()


def get_transport_manager() -> TransportManager:
    """Get the global transport manager."""
    return _transport_manager


def create_server(server_id: str, host: str = "127.0.0.1", port: int = 0) -> TCPServer:
    """Create a TCP server."""
    return _transport_manager.create_server(server_id, host, port)


def get_server(server_id: str) -> Optional[TCPServer]:
    """Get a server."""
    return _transport_manager.get_server(server_id)


def remove_server(server_id: str) -> None:
    """Remove a server."""
    _transport_manager.remove_server(server_id)


def stop_all() -> None:
    """Stop all servers."""
    _transport_manager.stop_all()