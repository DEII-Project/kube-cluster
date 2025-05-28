# Instructions for Setting Up a Kubernetes Cluster with Ansible on OpenStack

## Creating the Ansible driver and Nodes

- `cd machines`
- `chmod +x cluster.sh`
- Configure the instances configurations in `config/instance/`
- `./cluster.sh create --nodes 4`
- Run `cat config/hosts.yaml` then pick the IP of the driver

## Bootstrapping the Kubernetes Cluster

- Connect to the driver instance using `ssh ansible@<driver_ip>` if the keypair is set up correctly both in the instance configuration
  and the openstack cloud.
- In the driver instance pull this repository using `git clone git@github.com:DEII-Project/kube-cluster.git`
- `cd kube-cluster/ansible`
- `ansible-playbook k8s.yml`

Now it is possible to interact with K8s using `kubectl` from the driver instance.

## Starting the Registry Service

### Create the Registry SSL Certificates

- `cd k8s/model-registry`
- `mkdir -p certs`
- `openssl req \
-newkey rsa:4096 -nodes -sha256 -keyout certs/registry.key \
-addext "subjectAltName = IP:<node-0 IP>" \
-x509 -days 3650 -out certs/registry.crt`

### Add the certificate to the Kubernetes secrets

- `kubectl create secret tls registry-cert \
--cert=certs/registry.crt \
--key=certs/registry.key \
-n registry-certs`
- `kubectl apply -f all.yaml`

### Configure ansible master to trust the registry certificate

- `sudo cp certs/ /usr/local/share/ca-certificates/registry.crt`
- `sudo update-ca-certificates`

Now the registry service is running and can be accessed at `<node-0 IP>:32228` both from the driver instance and from the other nodes.

## Starting the App service

## CI/CD



.
├── ansible
│   ├── configs
│   └── playbooks
├── k8s
│   ├── deployments
│   └── model-registry
├── model_serving
│   ├── ci_cd
│   ├── openstack-client
│   ├── single_server_with_docker
│   └── single_server_without_docker
└── openstack
    ├── __pycache__
    ├── config
    └── credentials
