import uvicorn

from pilot.config.common import CommonConfig
from pilot.core.agent.app import create_app
from pilot.core.server import Server


def main() -> None:
    """Serve the Pilot agent on the address and port in common_config.toml."""
    config = CommonConfig.read(Server().benches_dir)
    uvicorn.run(create_app(config), host=config.agent.address, port=config.agent.port)


if __name__ == "__main__":
    main()
