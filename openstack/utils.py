from copy import copy
from openstack import connection
from openstack.config import loader
import yaml
from cryptography.hazmat.primitives import serialization as crypto_serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend as crypto_default_backend
from concurrent.futures import ThreadPoolExecutor, as_completed


def read_instance_config(filename):
    with open(filename, "r") as file:
        instance_config = yaml.safe_load(file)
    return instance_config


def get_instance_ips(instance):
    ips = {"floating": None, "fixed": None}
    if instance.addresses:
        for _, addresses_info in instance.addresses.items():
            for a in addresses_info:
                if a["OS-EXT-IPS:type"] == "floating":
                    ips["floating"] = a["addr"]
                else:
                    ips["fixed"] = a["addr"]
            return ips
    else:
        return None


def create_instance(conn, instance_dict_or_path, userdata_filename, postfix=""):
    if isinstance(instance_dict_or_path, str):
        instance_config = read_instance_config(instance_dict_or_path)
    else:
        instance_config = copy(instance_dict_or_path)
    instance_config["name"] = f"{instance_config['name']}{postfix}"
    with open(userdata_filename, "r") as file:
        server = conn.create_server(
            **instance_config, wait=True, userdata=file, timeout=600
        )
        ips = get_instance_ips(server)
    print(f"Created server: {server.name} with ID: {server.id} and IP: {ips}")
    return server, ips


def delete_instance(conn, instance_id):
    try:
        if instance_id is None:
            print("No instance ID provided.")
            return
        server = conn.get_server(instance_id)
        if server:
            conn.delete_server(server.id, wait=True)
            print(f"Deleted server: {server.name} with ID: {server.id}")
        else:
            print(f"Server with ID {instance_id} not found.")
    except Exception as e:
        print(f"Error deleting server: {e}")


def create_connection(clouds_filename):
    config = loader.OpenStackConfig(config_files=[clouds_filename]).get_one_cloud(
        "openstack"
    )
    conn = connection.from_config(cloud="openstack", config=config)
    return conn


def create_nodes(conn, instance_config, userdata, n_nodes, ansible_public_key):
    nodes = [None for _ in range(n_nodes)]
    updated_userdata = update_userdata(userdata, ansible_public_key=ansible_public_key)
    with ThreadPoolExecutor(max_workers=n_nodes) as executor:
        futures_to_nodeid = {}
        for i in range(n_nodes):
            future = executor.submit(
                create_instance,
                conn,
                instance_config,
                updated_userdata,
                postfix=f"-node-{i}",
            )
            futures_to_nodeid[future] = i
        for future in as_completed(futures_to_nodeid):
            node_idx = futures_to_nodeid[future]
            node_instance, node_ips = future.result()
            node_host = {}
            node_host["addresses"] = node_ips
            node_host["id"] = node_instance.id
            nodes[node_idx] = node_host
    return nodes


def create_ansible_inventory(nodes):
    inventory = {
        "kube_control_plane": {
            "hosts": {
                "node-0": {
                    "host": nodes[0]["addresses"]["fixed"],
                    "ansible_ssh_common_args": "-o StrictHostKeyChecking=no",
                }
            },
        },
        "kube_workers": {
            "hosts": {
                f"node-{i + 1}": {
                    "host": node["addresses"]["fixed"],
                    "ansible_ssh_common_args": "-o StrictHostKeyChecking=no",
                }
                for i, node in enumerate(nodes[1:])
            }
        },
    }
    return yaml.dump(inventory, default_flow_style=True)


def create_hostnames(nodes):
    hostnames = {}
    for i, node in enumerate(nodes):
        hostnames[f"node-{i}"] = node["addresses"]["fixed"]
    hosts_string = "\n".join([f"{v} {k}" for k, v in hostnames.items()])
    return hosts_string


def generate_ssh_keys():
    key = rsa.generate_private_key(
        backend=crypto_default_backend(), public_exponent=65537, key_size=2048
    )

    ansible_private_key = key.private_bytes(
        crypto_serialization.Encoding.PEM,
        crypto_serialization.PrivateFormat.PKCS8,
        crypto_serialization.NoEncryption(),
    )

    ansible_public_key = key.public_key().public_bytes(
        crypto_serialization.Encoding.OpenSSH, crypto_serialization.PublicFormat.OpenSSH
    )

    with open("credentials/keys/id_rsa", "wb") as file:
        file.write(ansible_private_key)
    with open("credentials/keys/id_rsa.pub", "wb") as file:
        file.write(ansible_public_key)
    return ansible_private_key, ansible_public_key


def create_git_deploy_key(ansible_public_key):
    from github import Github, Auth

    with open("credentials/github_token", "r") as file:
        github_token = file.read().strip()
    auth = Auth.Token(github_token)
    github = Github(auth=auth)
    repo = github.get_repo("DEII-Project/kube-cluster")

    previous_keys = repo.get_keys().get_page(0)
    for key in previous_keys:
        if key.title == "ansible":
            key.delete()
            print(f"Deleted previous key: {key.title}")

    repo.create_key(title="ansible", key=ansible_public_key.decode("utf-8"))


def update_userdata(
    userdata, nodes=None, ansible_private_key=None, ansible_public_key=None
):
    with open(userdata, "r") as file:
        userdata_content = file.read()
        userdata_content_updated = copy(userdata_content)
        if nodes:
            userdata_content_updated = userdata_content.replace(
                "NODENAMES", create_hostnames(nodes).replace("\n", "\\n")
            )
            userdata_content_updated = userdata_content_updated.replace(
                "ANSIBLE_INVENTORY",
                create_ansible_inventory(nodes).replace("\n", "\\n"),
            )
        if ansible_private_key:
            userdata_content_updated = userdata_content_updated.replace(
                "ANSIBLE_PRIVATE_KEY",
                ansible_private_key.decode("utf-8").strip().replace("\n", "\n      "),
            )
        if ansible_public_key:
            userdata_content_updated = userdata_content_updated.replace(
                "ANSIBLE_PUBLIC_KEY", ansible_public_key.decode("utf-8")
            )
    new_userdata = f"{userdata[:-5]}_sensitive.yaml"
    with open(new_userdata, "w") as file:
        file.write(userdata_content_updated)
    return new_userdata


def create_driver(
    conn, instance_config, userdata, nodes, ansible_private_key, ansible_public_key
):
    updated_userdata = update_userdata(
        userdata, nodes, ansible_private_key, ansible_public_key
    )
    driver_instance, driver_ips = create_instance(
        conn, instance_config, updated_userdata, postfix="-driver"
    )
    driver_host = {}
    driver_host["addresses"] = driver_ips
    driver_host["id"] = driver_instance.id
    return driver_host


def create_cluster(args):
    hosts = {"driver": {}, "nodes": []}
    conn = create_connection("credentials/clouds.yaml")
    ansible_private_key, ansible_public_key = generate_ssh_keys()
    create_git_deploy_key(ansible_public_key)

    # Create nodes
    node_instance_config = read_instance_config("./config/instance/node.yaml")
    node_userdata = "./config/userdata/node_userdata.yaml"
    nodes = create_nodes(
        conn, node_instance_config, node_userdata, args.nodes, ansible_public_key
    )
    hosts["nodes"] = nodes
    with open("./config/hosts.yaml", "w") as file:
        yaml.dump(hosts, file)

    # Create driver
    driver_userdata = "./config/userdata/driver_userdata.yaml"
    driver_instance_config = read_instance_config("./config/instance/driver.yaml")
    driver = create_driver(
        conn,
        driver_instance_config,
        driver_userdata,
        nodes,
        ansible_private_key,
        ansible_public_key,
    )

    hosts["driver"] = driver

    with open("./config/hosts.yaml", "w") as file:
        yaml.dump(hosts, file)


def delete_nodes(args):
    conn = create_connection("credentials/clouds.yaml")
    with open(args.hosts_file, "r") as file:
        hosts = yaml.safe_load(file)
    driver_id = hosts["driver"].get("id")
    delete_instance(conn, driver_id)

    for node in hosts["nodes"]:
        node_id = node["id"]
        delete_instance(conn, node_id)

    hosts = {"driver": {}, "nodes": []}
    with open(args.hosts_file, "w") as file:
        yaml.dump(hosts, file)
