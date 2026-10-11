"""Real Modbus RTU drivers. Importing this package is the only path that loads pymodbus/pyserial.

The simulation, twin and API modules never import it unless a real (rtu) profile is served.
"""
