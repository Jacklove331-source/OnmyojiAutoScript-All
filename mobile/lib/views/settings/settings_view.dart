import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/controller/settings.dart';
import 'package:oasx/service/locale_service.dart';
import 'package:oasx/service/theme_service.dart';
import 'package:styled_widget/styled_widget.dart';

import 'package:oasx/config/translation/i18n_content.dart';
import 'package:oasx/views/layout/appbar.dart';

class SettingsView extends StatelessWidget {
  const SettingsView({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: buildPlatformAppBar(context),
      body: SingleChildScrollView(
          child: <Widget>[
        const _ThemeWidget().paddingAll(5),
        const _LanguageWidget().paddingAll(5),
        const _AiWidget().paddingAll(5),
        killServerButton(),
        _exitButton(),
      ].toColumn().alignment(Alignment.center)),
    );
  }

  Widget _exitButton() {
    return TextButton(
            onPressed: () => {Get.offAllNamed('/login')},
            child: Text('Log out'.tr))
        .constrained(minWidth: 180);
  }

  Widget killServerButton() {
    return TextButton(
            onPressed: () => {
                  Get.defaultDialog(
                    title: I18n.are_you_sure_kill.tr,
                    onCancel: () => {},
                    onConfirm: () async =>
                        await Get.find<SettingsController>().killServer(),
                  )
                },
            child: Text(I18n.kill_oas_server.tr))
        .constrained(minWidth: 180);
  }
}

class _ThemeWidget extends StatelessWidget {
  const _ThemeWidget();

  @override
  Widget build(BuildContext context) {
    final themeService = Get.find<ThemeService>();

    return Obx(() {
      final isDark = themeService.isDarkMode;
      return <Widget>[
        Text(I18n.change_theme.tr).paddingOnly(bottom: 5),
        IconButton(
          onPressed: () => themeService.switchTheme(),
          icon: const Icon(Icons.light_mode),
          selectedIcon: const Icon(Icons.dark_mode),
          isSelected: isDark,
        )
      ].toColumn();
    });
  }
}

class _LanguageWidget extends StatelessWidget {
  const _LanguageWidget();

  @override
  Widget build(BuildContext context) {
    final localeService = Get.find<LocaleService>();

    return <Widget>[
      Text(I18n.change_language.tr).paddingOnly(bottom: 5),
      Obx(() {
        final isSelected = switch (localeService.language.value) {
          'zh-CN' => [true, false],
          'en-US' => [false, true],
          _ => [true, false],
        };
        return ToggleButtons(
          isSelected: isSelected,
          onPressed: (index) {
            if (index == 0) {
              localeService.switchLanguage('zh-CN');
            } else {
              localeService.switchLanguage('en-US');
            }
          },
          borderRadius: BorderRadius.circular(10),
          children: <Widget>[
            Text(I18n.zh_cn.tr).paddingOnly(left: 10, right: 10),
            Text(I18n.en_us.tr).paddingOnly(left: 10, right: 10),
          ],
        ).constrained(maxHeight: 40);
      })
    ].toColumn();
  }
}

class _AiWidget extends StatefulWidget {
  const _AiWidget();

  @override
  State<_AiWidget> createState() => _AiWidgetState();
}

class _AiWidgetState extends State<_AiWidget> {
  final TextEditingController _keyController = TextEditingController();
  bool _enabled = false;
  String _mode = 'observe';

  @override
  void initState() {
    super.initState();
    final controller = Get.find<SettingsController>();
    _enabled = controller.aiEnabled;
    _mode = controller.aiMode;
    _keyController.text = controller.aiKey;
  }

  @override
  Widget build(BuildContext context) {
    return <Widget>[
      const Text('AI 自愈（DeepSeek）').paddingOnly(bottom: 5),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('启用 AI 自愈'),
        value: _enabled,
        onChanged: (value) => setState(() => _enabled = value),
      ),
      DropdownButtonFormField<String>(
        value: _mode,
        decoration: const InputDecoration(labelText: '模式'),
        items: const [
          DropdownMenuItem(value: 'observe', child: Text('observe：只分析，不点击')),
          DropdownMenuItem(value: 'auto', child: Text('auto：允许执行安全动作')),
        ],
        onChanged: (value) => setState(() => _mode = value ?? 'observe'),
      ).paddingOnly(bottom: 8),
      TextField(
        controller: _keyController,
        obscureText: true,
        decoration: const InputDecoration(
          labelText: 'DeepSeek API Key',
          hintText: 'sk-...',
          border: OutlineInputBorder(),
        ),
      ).paddingOnly(bottom: 8),
      const Text('保存后，下一个任务的截图自愈会按新配置执行。').paddingOnly(bottom: 8),
      FilledButton(
        onPressed: () async {
          await Get.find<SettingsController>().saveAiConfig(
            enabled: _enabled,
            mode: _mode,
            key: _keyController.text,
          );
        },
        child: const Text('保存 AI 设置'),
      ),
    ].toColumn();
  }
}
