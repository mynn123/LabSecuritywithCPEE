# LabSecuritywithCPEE
For Practical course

Here’s a clear and concise English introduction you can use

### System Overview

This project is a monitoring system built with **CPEE** and a **Raspberry Pi**, using two **E18-D80NK infrared sensors** and a **camera**.
When a person enters or leaves a room, the sensors detect the movement, and the camera automatically captures photos of the event.

The **`.xml` file** represents the **CPEE process model**, which orchestrates asynchronous interactions between CPEE and the Raspberry Pi.
The **`server.py`** script runs on the Raspberry Pi and handles sensor monitoring, camera control, image saving, and callback communication with CPEE.

Please note that, in addition to running the server locally on the Raspberry Pi, you also need to set up a **public network forwarding service** (such as **ngrok**) so that CPEE can access the Raspberry Pi endpoints from the internet.
