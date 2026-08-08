## machine-01

Role: Application Server

## machine-02

Role: API Gateway

## machine-03

Role: Backend Processing Service

## machine-04

Role: Database Service

## machine-05

Role: Worker / Notification Service

## Communication

machine-01 -> machine-02
machine-02 -> machine-03
machine-03 -> machine-04
machine-03 -> machine-05

## Failure scenarios (as example of anamalies)

1. High CPU
2. Memory leak
3. Backend crash
4. Database unavailable
5. API timeout
6. HTTP 500 errors
7. Disk saturation
8. Network degradation
