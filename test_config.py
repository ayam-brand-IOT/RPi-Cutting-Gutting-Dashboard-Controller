from config_loader import load_config


def main():
    cfg = load_config("config.yaml")
    assert cfg["devices"]["gutting_left"]["slave"] == 3
    assert cfg["devices"]["gutting_right"]["slave"] == 6
    assert cfg["devices"]["gutting_right"]["holding_registers"]["cip_on_ms"]["address"] == 10
    assert cfg["devices"]["vision_right"]["slave"] == 7
    assert cfg["devices"]["vision_right"]["holding_registers"]["ml_model"]["address"] == 0
    print("Configuration OK — héritage et adresses validés")


if __name__ == "__main__":
    main()
